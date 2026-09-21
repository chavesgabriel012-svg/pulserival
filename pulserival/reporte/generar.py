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
from . import validar as validar_mod

TITULO = "Reporte de anuncios de la competencia"


def enriquecer_anuncios(
    con: sqlite3.Connection,
    anuncios: list[dict[str, Any]],
    presupuesto: Presupuesto | None = None,
    forzar: bool = False,
) -> int:
    """Llama al modelo barato por cada anuncio que aún no tiene análisis."""
    procesados = 0
    for a in anuncios:
        if a.get("analisis") and not forzar:
            continue
        if a["clasificacion"] not in ("nuevo", "cambiado"):
            continue  # no gastamos IA en anuncios que ya reportamos antes
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
            continue
        analisis = resp.json()
        if not isinstance(analisis, dict):
            continue
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
        inicio, fin = util.periodo(cliente.get("periodicidad") or "semanal")

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

    enriquecer_anuncios(con, anuncios, presupuesto)

    contexto = {
        "cliente": cliente.get("nombre_empresa"),
        "industria": cliente.get("industria"),
        "notas_cliente": cliente.get("notas"),
        "periodo_inicio": inicio,
        "periodo_fin": fin,
        "competidores": competidores,
        "conteo": conteo,
        "anuncios": anuncios,
        "periodo_anterior": datos_mod.resumen_periodo_anterior(con, int(cliente["id"]), inicio),
    }

    if not anuncios:
        borrador = (
            "## Lo más importante de esta semana\n\n"
            f"En el periodo del {inicio} al {fin} no detectamos actividad publicitaria "
            f"nueva de {', '.join(competidores) or 'los competidores seguidos'} en las "
            "bibliotecas públicas de anuncios de Meta y Google.\n\n"
            "## Qué está haciendo cada competidor\n\n"
            "Sin movimientos detectados en el periodo.\n\n"
            "## Movimientos que vale la pena mirar de cerca\n\n"
            "Nada que reportar. Un periodo sin movimiento también es información: "
            "sugiere que la competencia no está probando ángulos nuevos.\n\n"
            "## Qué haría yo esta semana\n\n"
            "_(revisar y completar)_\n"
        )
        resp_proveedor, resp_modelo, costo, version = "-", "-", 0.0, "-"
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

    validacion = validar_mod.validar(borrador, anuncios)
    asunto = _asunto(cliente, inicio, fin, conteo)
    fila_datos = {
        "cliente_id": int(cliente["id"]),
        "periodo_inicio": inicio,
        "periodo_fin": fin,
        "asunto": asunto,
        "borrador_md": borrador,
        "datos_json": db.json_o_nada({"anuncios": anuncios, "conteo": conteo,
                                      "competidores": competidores}),
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
        "borrador_md": borrador,
        "conteo": conteo,
        "anuncios": anuncios,
        "validacion": validacion,
        "proveedor": resp_proveedor,
        "modelo": resp_modelo,
        "costo_usd": costo,
    }


def _asunto(cliente: dict, inicio: str, fin: str, conteo: dict[str, int]) -> str:
    movimiento = conteo.get("nuevo", 0) + conteo.get("cambiado", 0)
    cola = f"{movimiento} movimiento{'s' if movimiento != 1 else ''} de la competencia" if movimiento \
        else "sin movimientos nuevos"
    return f"{TITULO} · {fin} · {cola}"
