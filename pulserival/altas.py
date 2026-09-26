"""Dar de alta un cliente y sus competidores, desde donde sea.

Existe para que haya UNA sola forma de crear un cliente. Antes la lógica
estaba metida en `cli.cmd_clientes`, y el formulario de la landing habría
sido una segunda copia que se despega de la primera en la tercera semana.

Lo que valida es lo mismo que el pipeline exige después:
`pipeline._tiene_datos_para()` necesita, por plataforma, al menos uno de sus
campos. Un competidor sin ninguno de los dos no falla al guardarlo: falla
callado en la corrida del lunes, que es peor.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

from . import db, planes

MAX_COMPETIDORES = 8
# Cuántos anuncios se piden al verificar. Cuesta plata por anuncio, y para
# responder "¿este competidor pauta o no?" con diez alcanza.
LIMITE_VERIFICACION = 10


class AltaInvalida(ValueError):
    """Los datos que mandaron no alcanzan para dar de alta a nadie."""


def _texto(valor: Any) -> str:
    return (str(valor) if valor is not None else "").strip()


def clave_desde(nombre: str, existentes: set[str] | None = None) -> str:
    """Un id corto y estable a partir del nombre, para config/clientes.yaml."""
    plano = unicodedata.normalize("NFKD", _texto(nombre).lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9]+", "-", plano).strip("-") or "cliente"
    if not existentes or base not in existentes:
        return base
    n = 2
    while f"{base}-{n}" in existentes:
        n += 1
    return f"{base}-{n}"


def validar_email(email: str) -> str:
    email = _texto(email)
    # A propósito laxo: rechazar un correo válido raro es peor que aceptar
    # uno que rebota, porque el rebote se ve y el rechazo se pierde.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise AltaInvalida(f"El correo '{email}' no parece válido")
    return email


def normalizar_competidor(datos: dict[str, Any]) -> dict[str, Any]:
    """Deja un competidor listo para guardar, o explica qué le falta."""
    nombre = _texto(datos.get("nombre"))
    if not nombre:
        raise AltaInvalida("Un competidor sin nombre no se puede reportar")

    pagina = _texto(datos.get("meta_pagina_url"))
    consulta = _texto(datos.get("meta_consulta"))
    dominio = _texto(datos.get("google_dominio")).replace("https://", "").replace("http://", "")
    dominio = dominio.split("/")[0].strip()

    # Si mandaron algo en el campo de Facebook que no es una URL, sirve igual
    # como término de búsqueda: es lo que el actor usa cuando no hay página.
    if pagina and "facebook.com" not in pagina.lower():
        consulta = consulta or pagina
        pagina = ""
    if pagina and not pagina.lower().startswith("http"):
        pagina = "https://" + pagina.lstrip("/")

    if not (pagina or consulta or dominio):
        raise AltaInvalida(
            f"De '{nombre}' hace falta al menos su página de Facebook, un término "
            "de búsqueda, o su dominio. Sin ninguno de los tres no hay dónde buscar.")

    return {
        "nombre": nombre,
        "meta_pagina_url": pagina or None,
        "meta_consulta": consulta or (nombre if not pagina else None),
        "google_dominio": dominio or None,
        "prioridad": int(datos.get("prioridad") or 2),
    }


def verificar_competidor(competidor: dict[str, Any], plataforma: str = "meta",
                         limite: int = LIMITE_VERIFICACION,
                         modo: str = "auto") -> dict[str, Any]:
    """¿Este competidor tiene anuncios ahora mismo?

    Es el equivalente de `cli prueba-scraper`: la misma fuente, la misma
    llamada. Se corre ANTES de confirmar el alta para poder decirle al
    cliente "este competidor no está pautando" en el momento, en vez de que
    se entere cuando le llegue el primer reporte medio vacío.
    """
    from .fuentes import FuenteError, obtener_fuente

    entrada = dict(competidor)
    entrada.setdefault("id", 0)
    try:
        fuente = obtener_fuente(plataforma, modo)
        anuncios = fuente.traer(entrada, limite=limite)
    except FuenteError as e:
        return {"plataforma": plataforma, "encontrados": None, "error": str(e)}
    return {"plataforma": plataforma, "encontrados": len(anuncios), "error": None}


def crear(
    con: sqlite3.Connection,
    empresa: str,
    email: str,
    competidores: list[dict[str, Any]],
    plan: str = "semanal",
    contacto: str | None = None,
    whatsapp: str | None = None,
    industria: str | None = None,
    notas: str | None = None,
    estado: str | None = None,
    cadencia_dias: int | None = None,
    precio: float | None = None,
    pago_proveedor: str | None = None,
    pago_referencia: str | None = None,
) -> dict[str, Any]:
    """Crea el cliente y sus competidores. No verifica ni cobra: solo guarda.

    El estado por defecto NO es 'activa': un alta que llega de un formulario
    público no puede empezar a gastar scraper antes de que alguien confirme
    que pagó.
    """
    empresa = _texto(empresa)
    if not empresa:
        raise AltaInvalida("Falta el nombre de la empresa")
    email = validar_email(email)

    datos_plan = planes.plan(plan)
    if not datos_plan:
        raise AltaInvalida(
            f"El plan '{plan}' no existe. Disponibles: {', '.join(planes.PLANES_VALIDOS)}")

    if not competidores:
        raise AltaInvalida("Hace falta al menos un competidor para poder reportar")
    if len(competidores) > MAX_COMPETIDORES:
        raise AltaInvalida(
            f"Son {len(competidores)} competidores y el máximo es {MAX_COMPETIDORES}")
    limpios = [normalizar_competidor(c) for c in competidores]

    estado = estado or ("prueba_pendiente" if plan == "prueba" else "pendiente_pago")
    if estado not in planes.ESTADOS_VALIDOS:
        raise AltaInvalida(f"Estado de suscripción inválido: {estado}")

    ya_usadas = {
        f["clave"] for f in db.filas(con, "SELECT clave FROM clientes WHERE clave IS NOT NULL")
    }
    cliente_id = db.insertar(con, "clientes", {
        "clave": clave_desde(empresa, ya_usadas),
        "nombre_empresa": empresa,
        "contacto_nombre": _texto(contacto) or None,
        "contacto_email": email,
        "contacto_whatsapp": _texto(whatsapp) or None,
        "periodicidad": datos_plan.get("periodicidad") or "semanal",
        "plan": plan,
        "estado_suscripcion": estado,
        "cadencia_dias": cadencia_dias,
        "precio_mensual_usd": precio,
        "pago_proveedor": pago_proveedor,
        "pago_referencia": pago_referencia,
        "industria": _texto(industria) or None,
        "notas": _texto(notas) or None,
    })
    for orden, comp in enumerate(limpios, start=1):
        db.insertar(con, "competidores_seguidos", {
            "cliente_id": cliente_id,
            "clave": clave_desde(comp["nombre"]),
            **comp,
        })
    return {"cliente_id": cliente_id, "competidores": len(limpios), "estado": estado}


def activar(con: sqlite3.Connection, cliente_id: int,
            referencia: str | None = None,
            proveedor: str | None = None,
            precio: float | None = None) -> dict[str, Any]:
    """Marca la suscripción como pagada. Es lo único que habilita el gasto."""
    fila = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (cliente_id,))
    if not fila:
        raise AltaInvalida(f"No existe el cliente {cliente_id}")
    cambios: dict[str, Any] = {"estado_suscripcion": "activa", "activo": 1}
    if referencia:
        cambios["pago_referencia"] = _texto(referencia)
    if proveedor:
        cambios["pago_proveedor"] = _texto(proveedor)
    if precio is not None:
        cambios["precio_mensual_usd"] = precio
    db.actualizar(con, "clientes", cliente_id, cambios)
    return {"cliente_id": cliente_id, "empresa": fila["nombre_empresa"], "estado": "activa"}
