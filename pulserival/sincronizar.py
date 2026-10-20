"""Aplica la configuración de clientes a la base de datos.

Lee de dos lugares, en este orden:
  1. config/clientes.yaml, el archivo curado a mano;
  2. config/altas/*.yaml, uno por alta que llegó del formulario de la landing
     cuando el servidor web corre sin base propia (ver pulserival/web/deposito.py).

Si una clave aparece en los dos, gana la de clientes.yaml y la del alta se
saltea con un aviso: el archivo curado es la verdad, y una bandeja de entrada
pública no puede cambiar un cliente que ya existe.

La base de datos no se versiona (tiene datos que cambian en cada corrida),
pero la lista de clientes y competidores sí debería: es configuración, no
datos. Sin esto, el servidor que corre el cron arranca con una base vacía y
no tiene a quién reportarle.

La operación es idempotente y conservadora:
  - un cliente o competidor nuevo en el archivo se crea;
  - uno que ya existe se actualiza solo en los campos que cambiaron;
  - uno que desaparece del archivo se DESACTIVA, nunca se borra: borrarlo
    se llevaría por delante todo el historial de anuncios detectados, que es
    justamente lo que permite decir "esto es nuevo".
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from . import config, db, planes

CAMPOS_CLIENTE = (
    "nombre_empresa", "contacto_nombre", "contacto_email", "contacto_whatsapp",
    "periodicidad", "plan", "estado_suscripcion", "cadencia_dias",
    "precio_mensual_usd", "pago_proveedor", "pago_referencia",
    "dia_envio", "industria", "notas", "origen", "ciclo_facturacion", "activo",
    "consentimiento_en", "consentimiento_version",
)

# Lo que escribe el webhook de Paddle. En un cliente que paga por Paddle,
# clientes.yaml no lo pisa: el YAML lo escribe una persona una vez, y Paddle
# sabe si la tarjeta de este mes pasó. Si `aplicar-config` volviera a poner
# 'activa' a alguien que Paddle marcó vencido, se le seguiría reportando sin
# que pague.
CAMPOS_DE_PADDLE = ("estado_suscripcion", "ciclo_facturacion",
                    "pago_proveedor", "pago_referencia")
CAMPOS_COMPETIDOR = (
    "nombre", "meta_pagina_url", "meta_pagina_id", "meta_consulta",
    "google_dominio", "google_anunciante", "google_anunciante_id",
    "prioridad", "notas", "activo",
)


class ConfigInvalida(ValueError):
    """El archivo tiene un error que hay que arreglar antes de seguir."""


def leer(ruta: Path | None = None) -> list[dict[str, Any]]:
    ruta = Path(ruta) if ruta else (config.DIR_CONFIG / "clientes.yaml")
    if not ruta.exists():
        raise ConfigInvalida(f"No existe {ruta}")
    return validar(yaml.safe_load(ruta.read_text(encoding="utf-8")) or {})


def validar(datos: dict[str, Any]) -> list[dict[str, Any]]:
    clientes = datos.get("clientes") or []
    if not isinstance(clientes, list):
        raise ConfigInvalida("La clave 'clientes' tiene que ser una lista")

    claves = set()
    for i, cliente in enumerate(clientes, start=1):
        clave = cliente.get("clave")
        if not clave:
            raise ConfigInvalida(f"El cliente #{i} no tiene 'clave' (un id corto y estable)")
        if clave in claves:
            raise ConfigInvalida(f"La clave '{clave}' está repetida")
        claves.add(clave)
        if not cliente.get("empresa"):
            raise ConfigInvalida(f"El cliente '{clave}' no tiene 'empresa'")
        if not cliente.get("contacto_email"):
            raise ConfigInvalida(f"El cliente '{clave}' no tiene 'contacto_email'")
        periodicidad = cliente.get("periodicidad", "semanal")
        if periodicidad not in planes.PERIODICIDADES:
            raise ConfigInvalida(f"El cliente '{clave}': periodicidad debe ser "
                                 f"{', '.join(planes.PERIODICIDADES)}")
        # Lo mismo que exige el CHECK de la base, dicho antes de llegar ahí.
        cadencia = cliente.get("cadencia_dias")
        if periodicidad == "custom" and not (isinstance(cadencia, int) and cadencia >= 1):
            raise ConfigInvalida(f"El cliente '{clave}' es custom: necesita cadencia_dias "
                                 "(cada cuántos días, 1 o más)")
        if periodicidad != "custom" and cadencia is not None:
            raise ConfigInvalida(f"El cliente '{clave}': cadencia_dias es solo para "
                                 f"periodicidad custom ('{periodicidad}' ya dice cada cuánto)")
        ciclo = cliente.get("ciclo_facturacion")
        if ciclo is not None and ciclo not in planes.CICLOS_VALIDOS:
            raise ConfigInvalida(f"El cliente '{clave}': ciclo_facturacion debe ser "
                                 f"{', '.join(planes.CICLOS_VALIDOS)}")
        competidores = cliente.get("competidores") or []
        claves_comp = set()
        for competidor in competidores:
            nombre = competidor.get("nombre")
            if not nombre:
                raise ConfigInvalida(f"Un competidor de '{clave}' no tiene 'nombre'")
            clave_comp = competidor.get("clave") or nombre
            if clave_comp in claves_comp:
                raise ConfigInvalida(f"El competidor '{clave_comp}' está repetido en '{clave}'")
            claves_comp.add(clave_comp)
            if not any(competidor.get(c) for c in
                       ("meta_pagina_url", "meta_consulta", "meta_pagina_id",
                        "google_dominio", "google_anunciante", "google_anunciante_id")):
                raise ConfigInvalida(
                    f"El competidor '{nombre}' de '{clave}' no tiene ni datos de Meta ni de "
                    "Google: no habría de dónde traer anuncios"
                )
    return clientes


def leer_altas(directorio: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Las altas que llegaron del formulario, cada una en su archivo.

    Un archivo malo NO tira la corrida: se saltea y se avisa. Es una bandeja de
    entrada escrita por un proceso web con datos que cargó un desconocido; que
    una de esas entradas pueda dejar sin reporte a los clientes que sí pagaron
    sería el peor de los dos errores posibles.
    """
    directorio = Path(directorio) if directorio else (config.DIR_CONFIG / "altas")
    if not directorio.is_dir():
        return [], []
    clientes: list[dict[str, Any]] = []
    avisos: list[str] = []
    # Orden alfabético para que sea determinista: los nombres empiezan con la
    # fecha, así que la primera solicitud gana los empates de clave.
    for ruta in sorted(directorio.glob("*.yaml")):
        try:
            entradas = validar(yaml.safe_load(ruta.read_text(encoding="utf-8")) or {})
        except (ConfigInvalida, yaml.YAMLError) as e:
            avisos.append(f"{ruta.name} se salteó: {e}")
            continue
        clientes.extend(entradas)
    return clientes, avisos


def leer_todo(ruta: Path | None = None,
              directorio: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """clientes.yaml más las altas, sin claves repetidas."""
    clientes = leer(ruta)
    vistas = {c["clave"] for c in clientes}
    altas, avisos = leer_altas(directorio)
    for entrada in altas:
        if entrada["clave"] in vistas:
            avisos.append(
                f"el alta '{entrada['clave']}' se salteó: esa clave ya está en "
                "clientes.yaml (o en un alta anterior)")
            continue
        vistas.add(entrada["clave"])
        clientes.append(entrada)
    return clientes, avisos


def aplicar(con: sqlite3.Connection, ruta: Path | None = None) -> dict[str, Any]:
    # Con --archivo se usa SOLO ese archivo: es la forma de aplicar una
    # configuración de prueba sin que se le mezcle la bandeja de altas.
    if ruta:
        clientes, avisos = leer(ruta), []
    else:
        clientes, avisos = leer_todo()
    resumen = {"creados": [], "actualizados": [], "desactivados": [],
               "competidores_creados": [], "competidores_actualizados": [],
               "competidores_desactivados": [], "avisos": avisos}

    claves_en_archivo = set()
    for entrada in clientes:
        clave = entrada["clave"]
        claves_en_archivo.add(clave)
        deseado = {
            "clave": clave,
            "nombre_empresa": entrada.get("empresa"),
            "contacto_nombre": entrada.get("contacto_nombre"),
            "contacto_email": entrada.get("contacto_email"),
            "contacto_whatsapp": entrada.get("contacto_whatsapp"),
            "periodicidad": entrada.get("periodicidad", "semanal"),
            # El plan manda sobre la periodicidad para calcular la cadencia;
            # los clientes de antes de los planes no traen nada de esto y
            # siguen funcionando con la periodicidad sola.
            "plan": entrada.get("plan"),
            "estado_suscripcion": entrada.get("estado_suscripcion"),
            "cadencia_dias": entrada.get("cadencia_dias"),
            "precio_mensual_usd": entrada.get("precio_mensual_usd"),
            "pago_proveedor": entrada.get("pago_proveedor"),
            "pago_referencia": entrada.get("pago_referencia"),
            "dia_envio": entrada.get("dia_envio", "martes"),
            "industria": entrada.get("industria"),
            "notas": (entrada.get("notas") or "").strip() or None,
            # Viene del alta: si no se copia aca, pasar la entrada de
            # config/altas/ a config/clientes.yaml pierde la respuesta de la
            # encuesta sin que nadie se entere.
            "origen": (entrada.get("origen") or "").strip() or None,
            "ciclo_facturacion": entrada.get("ciclo_facturacion"),
            "activo": 1 if entrada.get("activo", True) else 0,
        }
        # La constancia de que aceptó los términos viene del alta web. Se
        # copia si el YAML la trae, pero un YAML sin ella no la borra: es la
        # prueba del consentimiento y no se recupera.
        for campo in ("consentimiento_en", "consentimiento_version"):
            if entrada.get(campo):
                deseado[campo] = str(entrada[campo])
        existente = db.fila(con, "SELECT * FROM clientes WHERE clave = ?", (clave,))
        if existente:
            de_paddle = bool(db.valor(existente, "paddle_suscripcion_id"))
            cambios = {c: deseado[c] for c in CAMPOS_CLIENTE
                       if c in deseado and deseado[c] != existente[c]
                       and not (de_paddle and c in CAMPOS_DE_PADDLE)}
            if de_paddle and deseado["activo"] == 1:
                # Activo lo decide el pago, no el YAML. Que el YAML diga
                # activo: true no reactiva a alguien que Paddle dio de baja:
                # eso ya lo cubre `estado_suscripcion`, que el YAML no toca.
                cambios.pop("activo", None)
            if cambios:
                db.actualizar(con, "clientes", int(existente["id"]), cambios)
                resumen["actualizados"].append(f"{clave} ({', '.join(cambios)})")
            cliente_id = int(existente["id"])
        else:
            cliente_id = db.insertar(con, "clientes", deseado)
            resumen["creados"].append(clave)

        _aplicar_competidores(con, cliente_id, clave, entrada.get("competidores") or [], resumen)

    # Clientes que ya no están en el archivo: se desactivan, no se borran.
    # Menos los que pagan por Paddle: los que entran por la web nunca
    # estuvieron en clientes.yaml, y desactivarlos los dejaría pagando sin
    # recibir nada.
    for fila in db.filas(con, "SELECT * FROM clientes WHERE clave IS NOT NULL AND activo = 1 "
                              "AND paddle_suscripcion_id IS NULL"):
        if fila["clave"] not in claves_en_archivo:
            db.actualizar(con, "clientes", int(fila["id"]), {"activo": 0})
            resumen["desactivados"].append(fila["clave"])

    return resumen


def _aplicar_competidores(
    con: sqlite3.Connection, cliente_id: int, clave_cliente: str,
    competidores: list[dict], resumen: dict,
) -> None:
    claves = set()
    for entrada in competidores:
        clave = entrada.get("clave") or entrada["nombre"]
        claves.add(clave)
        deseado = {
            "cliente_id": cliente_id,
            "clave": clave,
            "nombre": entrada.get("nombre"),
            "meta_pagina_url": entrada.get("meta_pagina_url"),
            "meta_pagina_id": entrada.get("meta_pagina_id"),
            "meta_consulta": entrada.get("meta_consulta"),
            "google_dominio": entrada.get("google_dominio"),
            "google_anunciante": entrada.get("google_anunciante"),
            "google_anunciante_id": entrada.get("google_anunciante_id"),
            "prioridad": int(entrada.get("prioridad", 2)),
            "notas": (entrada.get("notas") or "").strip() or None,
            "activo": 1 if entrada.get("activo", True) else 0,
        }
        existente = db.fila(
            con, "SELECT * FROM competidores_seguidos WHERE cliente_id = ? AND clave = ?",
            (cliente_id, clave),
        )
        if existente:
            cambios = {c: deseado[c] for c in CAMPOS_COMPETIDOR
                       if c in deseado and deseado[c] != existente[c]}
            if cambios:
                db.actualizar(con, "competidores_seguidos", int(existente["id"]), cambios)
                resumen["competidores_actualizados"].append(f"{clave_cliente}/{clave}")
        else:
            db.insertar(con, "competidores_seguidos", deseado)
            resumen["competidores_creados"].append(f"{clave_cliente}/{clave}")

    for fila in db.filas(
        con, "SELECT * FROM competidores_seguidos WHERE cliente_id = ? AND clave IS NOT NULL "
             "AND activo = 1", (cliente_id,),
    ):
        if fila["clave"] not in claves:
            db.actualizar(con, "competidores_seguidos", int(fila["id"]), {"activo": 0})
            resumen["competidores_desactivados"].append(f"{clave_cliente}/{fila['clave']}")


def formatear(resumen: dict[str, Any]) -> str:
    etiquetas = {
        "creados": "clientes creados", "actualizados": "clientes actualizados",
        "desactivados": "clientes desactivados",
        "competidores_creados": "competidores creados",
        "competidores_actualizados": "competidores actualizados",
        "competidores_desactivados": "competidores desactivados",
    }
    lineas = [f"{etiqueta}: {', '.join(resumen[campo])}"
              for campo, etiqueta in etiquetas.items() if resumen[campo]]
    if not lineas:
        lineas = ["Sin cambios: la base ya coincide con el archivo."]
    # Los avisos van al final y con marca, porque piden que alguien los mire.
    for aviso in resumen.get("avisos") or []:
        lineas.append(f"AVISO · {aviso}")
    return "\n".join(lineas)
