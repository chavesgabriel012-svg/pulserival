"""Arma el insumo exacto del reporte a partir de la base de datos.

Importante: el reporte NO se genera desde lo que devolvió el scraper en vivo,
se genera desde lo que quedó guardado. Así el borrador siempre se puede
reproducir y auditar después ("¿de dónde salió esta frase?").
"""
from __future__ import annotations

import sqlite3
from typing import Any

import re

from .. import db, util

# Los anuncios de catálogo dinámico traen plantillas en vez de texto
# ("{{product.brand}}"). Meta las rellena al mostrarlas, pero la biblioteca
# pública las devuelve crudas: citarlas en el reporte sería ridículo.
PLANTILLA = re.compile(r"\{\{[^}]+\}\}")

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

        texto = util.sin_emojis(util.recortar(f["texto"], 700))
        es_plantilla = bool(texto) and bool(PLANTILLA.fullmatch(texto.strip())) or (
            bool(texto) and len(PLANTILLA.sub("", texto).strip()) < 12
        )
        item = {
            "anuncio_id": int(f["id"]),
            "clasificacion": clase,
            "competidor": f["competidor"],
            "prioridad": f["prioridad"],
            "plataforma": f["plataforma"],
            "titulo": None if es_plantilla else util.sin_emojis(f["titulo"]),
            "texto": None if es_plantilla else texto,
            # Sin texto no se puede hablar del mensaje del anuncio: el reporte
            # solo puede describir formato, fechas y actividad.
            "sin_texto": es_plantilla or not (texto or f["titulo"]),
            "es_catalogo_dinamico": es_plantilla,
            "descripcion": util.sin_emojis(f["descripcion"]),
            "cta": f["cta"],
            "link_destino": f["link_destino"],
            "tipo_creativo": f["tipo_creativo"],
            "url_anuncio": f["url_anuncio"],
            "fecha_inicio": f["fecha_inicio"],
            "visto_primero_en": primero,
            "visto_ultimo_en": ultimo,
            "analisis": db.leer_json(f["analisis_json"]),
            "version_anterior": util.recortar(anteriores[-1]["texto"], 200) if anteriores else None,
            "metadata": db.leer_json(f["metadata_json"], {}) or {},
        }
        salida.append(item)

    salida = _agrupar_variantes(salida)
    salida.sort(key=lambda x: (ORDEN[x["clasificacion"]], x["prioridad"] or 9, x["competidor"] or ""))
    for i, item in enumerate(salida, start=1):
        item["referencia"] = f"[A{i}]"
    return salida


def _agrupar_variantes(anuncios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Junta en una sola entrada los anuncios con el mismo texto.

    Un anunciante suele correr el mismo aviso en varias piezas: una por sede,
    una por público, una por ubicación. La biblioteca los devuelve como
    anuncios distintos, y sin agrupar el reporte repetiría seis veces el mismo
    mensaje y el cliente pensaría que no lo leímos.

    Se agrupa solo cuando hay texto: sin texto (el caso de Google) no se puede
    saber si dos piezas dicen lo mismo, así que cada una queda aparte.
    """
    grupos: dict[tuple, dict[str, Any]] = {}
    salida: list[dict[str, Any]] = []
    for item in anuncios:
        texto = util.normalizar_texto(item.get("texto"))[:300]
        if not texto:
            salida.append(item)
            continue
        clave = (item["competidor"], item["plataforma"], item["clasificacion"], texto)
        principal = grupos.get(clave)
        if principal is None:
            item["variantes"] = 1
            item["variantes_ids"] = [item["anuncio_id"]]
            grupos[clave] = item
            salida.append(item)
            continue
        principal["variantes"] += 1
        principal["variantes_ids"].append(item["anuncio_id"])
        # Nos quedamos con la fecha de inicio más temprana del grupo.
        if (item.get("fecha_inicio") or "9999") < (principal.get("fecha_inicio") or "9999"):
            principal["fecha_inicio"] = item["fecha_inicio"]
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


def agrupar_por_competidor(anuncios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los anuncios ordenados por competidor, para el detalle del reporte.

    Una lista plana de treinta anuncios no se puede leer. Agrupados por
    competidor —y dentro, por plataforma— el cliente encuentra de un vistazo
    lo suyo: "Monge, 7 anuncios" y abajo el desglose con su enlace.
    """
    grupos: dict[str, dict[str, Any]] = {}
    for a in anuncios:
        nombre = a["competidor"]
        g = grupos.setdefault(nombre, {
            "competidor": nombre, "prioridad": a.get("prioridad") or 9,
            "total": 0, "piezas": 0, "meta": [], "google": [],
        })
        g["total"] += 1
        g["piezas"] += a.get("variantes", 1)
        g[a["plataforma"]].append(a)
    orden = {"nuevo": 0, "cambiado": 1, "continua": 2, "pausado": 3}
    for g in grupos.values():
        for plataforma in ("meta", "google"):
            g[plataforma].sort(key=lambda x: (orden.get(x["clasificacion"], 9),
                                              x.get("fecha_inicio") or ""))
    return sorted(grupos.values(), key=lambda g: (g["prioridad"], -g["total"]))
