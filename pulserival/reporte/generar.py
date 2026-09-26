"""Paso 3 del pipeline: GENERAR el borrador con IA.

Dos llamadas de IA distintas, a propósito:

  1. Por cada anuncio nuevo o cambiado -> modelo barato y rápido (alto volumen).
     Saca campos estructurados: ángulo, tipo de oferta, precios, urgencia.
     El resultado se guarda en el anuncio, así nunca se paga dos veces por el
     mismo anuncio.
  2. Una sola vez por reporte -> el modelo bueno (calidad).
     Escribe el texto interpretativo que va a leer el cliente.

Separar las dos tareas es lo que mantiene el costo en centavos por reporte.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db, util
from ..ia import Peticion, Presupuesto, ProveedorError, ejecutar
from ..ia import prompts
from . import datos as datos_mod
from . import metricas as metricas_mod
from . import validar as validar_mod

TITULO = "Reporte de anuncios de la competencia"


def enriquecer_anuncios(
    con: sqlite3.Connection,
    anuncios: list[dict[str, Any]],
    presupuesto: Presupuesto | None = None,
    forzar: bool = False,
    maximo: int | None = None,
) -> int:
    """Llama al modelo barato por cada anuncio que aún no tiene análisis.

    Con un tope: son los tiers gratuitos los que pagan esto, y cien llamadas
    seguidas terminan en 429 para todo lo que venga después, incluida la
    redacción del reporte, que es lo que de verdad importa. Se priorizan los
    competidores de prioridad 1 y los anuncios más recientes; los que quedan
    fuera igual salen en el reporte con su texto y su enlace, solo que sin la
    lectura previa.
    """
    from .. import config as _config

    maximo = _config.max_anuncios_analizados() if maximo is None else maximo
    pendientes = [
        a for a in anuncios
        if a["clasificacion"] in ("nuevo", "cambiado")
        and (forzar or not a.get("analisis")
             or (a["analisis"] or {}).get("generado_por") == "reglas")
    ]
    pendientes.sort(key=lambda a: (a.get("prioridad") or 9,
                                   -(a.get("variantes") or 1),
                                   a.get("fecha_inicio") or ""), reverse=False)
    procesados = 0
    fallos_seguidos = 0
    for a in pendientes[:maximo]:
        if fallos_seguidos >= 3:
            # La fuente de IA está caída o sin cuota. Seguir insistiendo solo
            # consume lo que le queda a la redacción del reporte, que es la
            # llamada que de verdad importa.
            break
        sistema, usuario = prompts.armar("analizar_anuncio", **a)
        try:
            resp = ejecutar(
                Peticion(
                    tarea="analizar_anuncio",
                    sistema=sistema,
                    usuario=usuario,
                    datos=a,
                    json_estricto=True,
                ),
                con=con,
                presupuesto=presupuesto,
            )
        except ProveedorError:
            fallos_seguidos += 1
            continue
        analisis = resp.json()
        if not isinstance(analisis, dict):
            fallos_seguidos += 1
            continue
        fallos_seguidos = 0
        a["analisis"] = analisis
        db.actualizar(con, "anuncios_detectados", a["anuncio_id"],
                      {"analisis_json": db.json_o_nada(analisis)})
        procesados += 1
    return procesados


def generar(
    con: sqlite3.Connection,
    cliente: sqlite3.Row | dict,
    inicio: str | None = None,
    fin: str | None = None,
    presupuesto: Presupuesto | None = None,
    regenerar: bool = False,
) -> dict[str, Any]:
    """Genera (o regenera) el borrador del reporte de un cliente."""
    cliente = dict(cliente)
    if not inicio or not fin:
        # Mismo criterio que el pipeline: la cadencia del plan le gana al
        # enum viejo. Acá solo se usa cuando alguien llama a generar() sin
        # fechas (CLI suelto); el cron siempre las pasa calculadas.
        from .. import planes as planes_mod

        inicio, fin = planes_mod.periodo_de(cliente)

    existente = db.fila(
        con,
        "SELECT * FROM reportes_generados WHERE cliente_id = ? AND periodo_inicio = ? AND periodo_fin = ?",
        (cliente["id"], inicio, fin),
    )
    if existente and not regenerar:
        return {"reporte_id": int(existente["id"]), "ya_existia": True,
                "estado": existente["estado"], "borrador_md": existente["borrador_md"],
                "validacion": db.leer_json(existente["validacion_json"], {})}
    if existente and existente["estado"] == "enviado" and regenerar:
        raise RuntimeError(
            f"El reporte {existente['id']} ya fue enviado al cliente; no se regenera. "
            "Generá el del periodo siguiente."
        )

    anuncios = datos_mod.clasificar(con, int(cliente["id"]), inicio, fin)
    competidores = [dict(c)["nombre"] for c in db.competidores_de(con, int(cliente["id"]))]
    conteo = datos_mod.conteo(anuncios)
    # Las señales se calculan en código, no las estima el modelo: contar es
    # justo lo que los modelos hacen mal, y acá los números tienen que ser
    # exactos porque el cliente los puede verificar uno por uno.
    senales = metricas_mod.por_competidor(anuncios, inicio, fin)

    enriquecer_anuncios(con, anuncios, presupuesto)

    # El modelo ve una selección; el reporte los lista todos. Con 99 anuncios
    # el prompt se pasaba del límite de tamaño de algunos modelos (413).
    anuncios_prompt = _seleccionar_para_prompt(anuncios)
    contexto = {
        "cliente": cliente.get("nombre_empresa"),
        "industria": cliente.get("industria"),
        "notas_cliente": cliente.get("notas"),
        "periodo_inicio": inicio,
        "periodo_fin": fin,
        "competidores": competidores,
        "conteo": conteo,
        "anuncios": anuncios_prompt,
        "anuncios_omitidos": len(anuncios) - len(anuncios_prompt),
        "anuncios_totales": len(anuncios),
        "senales": metricas_mod.resumir_para_prompt(senales),
        "periodo_anterior": datos_mod.resumen_periodo_anterior(con, int(cliente["id"]), inicio),
    }

    if not anuncios:
        borrador = (
            "## Resumen ejecutivo\n\n"
            f"En el periodo del {inicio} al {fin} no detectamos actividad publicitaria "
            f"nueva de {', '.join(competidores) or 'los competidores seguidos'} en las "
            "bibliotecas públicas de anuncios de Meta y Google.\n\n"
            "## Panorama de la competencia\n\n"
            "Ningún competidor seguido tuvo anuncios detectables en el periodo.\n\n"
            "## Qué está haciendo cada competidor\n\n"
            "Sin movimientos detectados en el periodo.\n\n"
            "## Movimientos que vale la pena mirar de cerca\n\n"
            "Nada que reportar. Un periodo sin movimiento también es información: "
            "sugiere que la competencia no está probando ángulos nuevos.\n\n"
            "## Qué haría yo esta semana\n\n"
            "_(revisar y completar)_\n"
        )
        resp_proveedor, resp_modelo, costo, version = "-", "-", 0.0, "-"
        anuncios_prompt = anuncios
    else:
        sistema, usuario = prompts.armar("redactar_reporte", **contexto)
        resp = ejecutar(
            Peticion(tarea="redactar_reporte", sistema=sistema, usuario=usuario, datos=contexto),
            con=con,
            presupuesto=presupuesto,
        )
        borrador = resp.texto.strip()
        resp_proveedor, resp_modelo, costo = resp.proveedor, resp.modelo, resp.costo_usd
        version = prompts.version("redactar_reporte")

    validacion = validar_mod.validar(borrador, anuncios, competidores,
                                     anuncios_vistos=anuncios_prompt,
                                     proveedor=resp_proveedor)
    asunto = _asunto(cliente, inicio, fin, conteo)
    fila_datos = {
        "cliente_id": int(cliente["id"]),
        "periodo_inicio": inicio,
        "periodo_fin": fin,
        "asunto": asunto,
        "borrador_md": borrador,
        "datos_json": db.json_o_nada({"anuncios": anuncios, "conteo": conteo,
                                      "competidores": competidores, "senales": senales,
                                      "totales": metricas_mod.totales(senales)}),
        "estado": "borrador",
        "proveedor_ia": resp_proveedor,
        "modelo_ia": resp_modelo,
        "version_prompt": version,
        "costo_usd": round(costo, 6),
        "validacion_json": db.json_o_nada(validacion),
        "generado_en": util.ahora_iso(),
    }

    if existente:
        reporte_id = int(existente["id"])
        # Al regenerar, la versión final anterior queda obsoleta: se borra para
        # que `exportar` y `enviar` trabajen sobre el borrador nuevo y no sobre
        # el texto viejo. La edición registrada se conserva en su tabla.
        cambios = {k: v for k, v in fila_datos.items() if k != "cliente_id"}
        cambios["final_md"] = None
        db.actualizar(con, "reportes_generados", reporte_id, cambios)
        con.execute("DELETE FROM anuncios_en_reporte WHERE reporte_id = ?", (reporte_id,))
    else:
        reporte_id = db.insertar(con, "reportes_generados", fila_datos)

    for a in anuncios:
        con.execute(
            "INSERT OR REPLACE INTO anuncios_en_reporte (reporte_id, anuncio_id, referencia, clasificacion) "
            "VALUES (?, ?, ?, ?)",
            (reporte_id, a["anuncio_id"], a["referencia"], a["clasificacion"]),
        )

    return {
        "reporte_id": reporte_id,
        "ya_existia": False,
        "asunto": asunto,
        "senales": senales,
        "borrador_md": borrador,
        "conteo": conteo,
        "anuncios": anuncios,
        "validacion": validacion,
        "proveedor": resp_proveedor,
        "modelo": resp_modelo,
        "costo_usd": costo,
    }


def _seleccionar_para_prompt(anuncios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los anuncios más relevantes para escribir el análisis.

    Criterio: primero lo que es noticia (nuevo, cambiado, apagado), después
    el competidor prioritario, después el turno del competidor —para que dos
    competidores del mismo peso se reparten los cupos en vez de que uno se
    lleve todo—, después los mensajes con más variantes, que es donde el
    competidor pone más esfuerzo, y por último los que tienen texto, porque
    de los que no lo tienen no hay nada que interpretar.
    """
    from .. import config as _config

    orden = {"nuevo": 0, "cambiado": 1, "pausado": 2, "continua": 3}

    def clave_interna(a):
        return (
            orden.get(a["clasificacion"], 9),
            a.get("prioridad") or 9,
            1 if a.get("sin_texto") else 0,
            -(a.get("variantes") or 1),
        )

    # Turno de cada anuncio dentro de SU competidor: el mejor de cada uno es
    # turno 0, el siguiente turno 1. Entra en la clave después de la
    # prioridad, así que entre competidores del mismo peso se alternan.
    #
    # Sin esto el desempate lo definía el orden de entrada, que viene
    # ordenado por NOMBRE de competidor: dos competidores iguales se
    # repartían 27 y 6 según cuál iba primero en el alfabeto.
    turnos: dict[str, int] = {}
    con_turno = []
    for a in sorted(anuncios, key=clave_interna):
        comp = a.get("competidor") or ""
        con_turno.append((turnos.get(comp, 0), a))
        turnos[comp] = turnos.get(comp, 0) + 1

    def clave(par):
        turno, a = par
        k = clave_interna(a)
        return (k[0], k[1], turno, k[2], k[3])

    ordenados = [a for _, a in sorted(con_turno, key=clave)]
    tope = _config.max_anuncios_en_prompt()

    # Cupo mínimo por competidor ANTES de llenar por relevancia global.
    #
    # Sin esto, un competidor de prioridad 2 puede quedar invisible para el
    # análisis: Artelec entró con 28 anuncios activos y los 45 cupos se los
    # llevaron enteros los nuevos de SIMAN y Monge, que son prioridad 1 y
    # tenían 115. El modelo solo vio los totales de Artelec, así que lo
    # describió por formatos y días sin poder citar ni interpretar un
    # mensaje, y se perdió el único que le importaba al cliente: el de
    # "Crédito Artelec", que compite de frente con su crédito propio.
    competidores = {a.get("competidor") or "" for a in ordenados}
    minimo = _config.min_anuncios_por_competidor_en_prompt()
    # El cupo se recorta a la MITAD del presupuesto como techo, por dos
    # razones distintas:
    #   - Con 9 competidores y un cupo de 6 se consumían los 45 lugares antes
    #     de llegar al noveno, que volvía a quedar en cero.
    #   - Recortarlo solo a tope/competidores tampoco sirve: repartía los 45
    #     en 5 y 5 y la prioridad dejaba de decidir nada.
    # Con la mitad reservada al piso y la mitad al mérito, todos aparecen y
    # el competidor prioritario sigue llevándose la mayor parte.
    if competidores:
        minimo = max(1, min(minimo, max(1, (tope // 2) // len(competidores))))

    elegidos: list[int] = []
    por_competidor: dict[str, int] = {}
    for i, a in enumerate(ordenados):
        if len(elegidos) >= tope:
            break
        comp = a.get("competidor") or ""
        if por_competidor.get(comp, 0) < minimo:
            elegidos.append(i)
            por_competidor[comp] = por_competidor.get(comp, 0) + 1
    tomados = set(elegidos)
    for i in range(len(ordenados)):
        if len(tomados) >= tope:
            break
        tomados.add(i)
    # Se devuelven en el orden de relevancia, no en el del cupo.
    return [a for i, a in enumerate(ordenados) if i in tomados]


def _asunto(cliente: dict, inicio: str, fin: str, conteo: dict[str, int]) -> str:
    movimiento = conteo.get("nuevo", 0) + conteo.get("cambiado", 0)
    cola = f"{movimiento} movimiento{'s' if movimiento != 1 else ''} de la competencia" if movimiento \
        else "sin movimientos nuevos"
    return f"{TITULO} · {fin} · {cola}"
