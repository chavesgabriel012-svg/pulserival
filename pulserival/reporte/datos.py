"""Arma el insumo exacto del reporte a partir de la base de datos.

Importante: el reporte NO se genera desde lo que devolvió el scraper en vivo,
se genera desde lo que quedó guardado. Así el borrador siempre se puede
reproducir y auditar después ("¿de dónde salió esta frase?").
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db, util

CLASIFICACIONES = ("nuevo", "cambiado", "pausado", "continua")
ORDEN = {"nuevo": 0, "cambiado": 1, "pausado": 2, "continua": 3}


def clasificar(con: sqlite3.Connection, cliente_id: int, inicio: str, fin: str) -> list[dict[str, Any]]:
    """Devuelve los anuncios relevantes del periodo, ya clasificados.

    nuevo     -> lo detectamos por primera vez dentro del periodo y no existía
                 otra versión anterior del mismo anuncio
    cambiado  -> detectado en el periodo, pero ya conocíamos ese mismo anuncio
                 (mismo id de la plataforma) con otro contenido
    pausado   -> lo veníamos viendo y dejó de aparecer dentro del periodo
    continua  -> sigue corriendo igual desde antes del periodo
    """
    filas = db.filas(
        con,
        """
        SELECT a.*, c.nombre AS competidor, c.prioridad
        FROM anuncios_detectados a
        JOIN competidores_seguidos c ON c.id = a.competidor_id
        WHERE c.cliente_id = ?
        ORDER BY c.prioridad, a.visto_primero_en
        """,
        (cliente_id,),
    )

    # Historial por anuncio de la plataforma, para reconocer versiones.
    versiones: dict[tuple, list[sqlite3.Row]] = {}
    for f in filas:
        if f["id_externo"]:
            versiones.setdefault((f["competidor_id"], f["plataforma"], f["id_externo"]), []).append(f)

    salida: list[dict[str, Any]] = []
    for f in filas:
        primero = (f["visto_primero_en"] or "")[:10]
        ultimo = (f["visto_ultimo_en"] or "")[:10]
        en_periodo_alta = inicio <= primero <= fin
        en_periodo_baja = inicio <= ultimo <= fin

        # Versiones del mismo anuncio de la plataforma, ordenadas por cuándo
        # las vimos. El id de la fila desempata cuando dos versiones se
        # guardaron el mismo segundo.
        hermanas = versiones.get((f["competidor_id"], f["plataforma"], f["id_externo"]), [])
        orden_actual = ((f["visto_primero_en"] or ""), int(f["id"]))
        anteriores = [h for h in hermanas if ((h["visto_primero_en"] or ""), int(h["id"])) < orden_actual]
        posteriores = [h for h in hermanas if ((h["visto_primero_en"] or ""), int(h["id"])) > orden_actual]

        if posteriores:
            # Es una versión vieja de un anuncio que después cambió: el que
            # cuenta la historia es la versión nueva, no esta.
            continue
        if f["estado"] == "pausado" and en_periodo_baja:
            clase = "pausado"
        elif en_periodo_alta and anteriores:
            clase = "cambiado"
        elif en_periodo_alta:
            clase = "nuevo"
        elif f["estado"] == "activo" and ultimo >= inicio:
            clase = "continua"
        else:
            continue  # anuncio viejo que ya no toca este periodo

        item = {
            "anuncio_id": int(f["id"]),
            "clasificacion": clase,
            "competidor": f["competidor"],
            "prioridad": f["prioridad"],
            "plataforma": f["plataforma"],
            "titulo": f["titulo"],
            "texto": util.recortar(f["texto"], 700),
            "descripcion": f["descripcion"],
            "cta": f["cta"],
            "link_destino": f["link_destino"],
            "tipo_creativo": f["tipo_creativo"],
            "url_anuncio": f["url_anuncio"],
            "fecha_inicio": f["fecha_inicio"],
            "visto_primero_en": primero,
            "visto_ultimo_en": ultimo,
            "analisis": db.leer_json(f["analisis_json"]),
            "version_anterior": util.recortar(anteriores[-1]["texto"], 200) if anteriores else None,
        }
        salida.append(item)

    salida.sort(key=lambda x: (ORDEN[x["clasificacion"]], x["prioridad"] or 9, x["competidor"] or ""))
    for i, item in enumerate(salida, start=1):
        item["referencia"] = f"[A{i}]"
    return salida


def conteo(anuncios: list[dict]) -> dict[str, int]:
    return {c: sum(1 for a in anuncios if a["clasificacion"] == c) for c in CLASIFICACIONES}


def resumen_periodo_anterior(con: sqlite3.Connection, cliente_id: int, inicio: str) -> str:
    """Una línea por competidor con lo que ya sabíamos antes del periodo.
    Le da memoria al reporte sin inflar el prompt."""
    filas = db.filas(
        con,
        """
        SELECT c.nombre AS competidor, a.plataforma, COUNT(*) AS n
        FROM anuncios_detectados a
        JOIN competidores_seguidos c ON c.id = a.competidor_id
        WHERE c.cliente_id = ? AND date(a.visto_primero_en) < date(?)
        GROUP BY c.nombre, a.plataforma
        """,
        (cliente_id, inicio),
    )
    if not filas:
        return "Es el primer reporte de este cliente: no hay periodo anterior con el que comparar."
    return "; ".join(f"{f['competidor']} ({f['plataforma']}): {f['n']} anuncios ya conocidos" for f in filas)
