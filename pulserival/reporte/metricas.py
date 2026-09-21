"""Señales de presión publicitaria por competidor.

POR QUÉ EXISTE ESTE ARCHIVO
───────────────────────────
La pregunta que todo cliente hace es "¿cuánto está invirtiendo mi
competencia?". Ese dato NO es público: la Biblioteca de Anuncios de Meta y
el Centro de Transparencia de Google solo publican inversión y alcance para
anuncios políticos o de temas sociales, y para los entregados en la Unión
Europea. Para un comercio que pauta en Costa Rica, el campo `spend` viene
vacío. Verificado contra respuestas reales (ver tests/fixtures/).

Inventar una cifra de inversión, aunque sea "estimada", es la forma más
rápida de perder un cliente: basta con que la compare con lo que él mismo
gasta para que todo el reporte pierda credibilidad.

Lo que sí se puede medir, y es honesto porque cualquiera lo puede verificar
abriendo la biblioteca pública:

  · cuántos mensajes distintos tiene al aire
  · en cuántas piezas los repite (una por sede, por público, por producto)
  · hace cuánto sostiene el mensaje más viejo
  · con qué frecuencia lanza mensajes nuevos
  · en qué formatos (un video cuesta producirlo; un catálogo implica
    e-commerce conectado)
  · en cuántas plataformas los publica

Juntas, esas señales responden la pregunta de fondo —"¿me está apretando o
está tranquilo?"— sin inventar un número. Y son comparables entre
competidores, que es lo que al cliente le sirve para decidir.

A propósito NO se calcula un puntaje único de "presión". Cualquier fórmula
que combine estas señales tendría pesos arbitrarios y se leería como un dato
duro cuando no lo es. Se muestran las señales y el análisis las interpreta.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from .. import util


def por_competidor(anuncios: list[dict[str, Any]], inicio: str, fin: str) -> list[dict[str, Any]]:
    """Una fila por competidor y plataforma, con las señales medibles."""
    grupos: dict[tuple[str, str], list[dict]] = {}
    for a in anuncios:
        grupos.setdefault((a["competidor"], a["plataforma"]), []).append(a)

    filas = []
    for (competidor, plataforma), items in grupos.items():
        filas.append(_fila(competidor, plataforma, items, fin))
    filas.sort(key=lambda f: (-f["piezas"], f["competidor"]))
    return filas


def _fila(competidor: str, plataforma: str, items: list[dict], fin: str) -> dict[str, Any]:
    vivos = [a for a in items if a["clasificacion"] != "pausado"]
    piezas = sum(a.get("variantes", 1) for a in vivos)
    formatos = Counter(a.get("tipo_creativo") or "no_determinado" for a in vivos)

    plataformas: set[str] = set()
    dias_al_aire: list[int] = []
    for a in items:
        meta = a.get("metadata") or {}
        for p in (meta.get("plataformas_publicacion") or []):
            if isinstance(p, str):
                plataformas.add(p.title())
        dias = meta.get("dias_al_aire")
        if isinstance(dias, int):
            dias_al_aire.append(dias)

    antiguedades = [_antiguedad(a.get("fecha_inicio"), fin) for a in vivos]
    antiguedades = [d for d in antiguedades if d is not None]
    if not antiguedades and dias_al_aire:
        antiguedades = dias_al_aire

    return {
        "competidor": competidor,
        "plataforma": plataforma,
        "mensajes": len(vivos),
        "piezas": piezas,
        "nuevos": sum(1 for a in items if a["clasificacion"] == "nuevo"),
        "cambiados": sum(1 for a in items if a["clasificacion"] == "cambiado"),
        "apagados": sum(1 for a in items if a["clasificacion"] == "pausado"),
        "formatos": dict(formatos.most_common()),
        "formato_principal": formatos.most_common(1)[0][0] if formatos else None,
        "plataformas": sorted(plataformas),
        "variantes_max": max((a.get("variantes", 1) for a in vivos), default=0),
        "dias_mensaje_mas_viejo": max(antiguedades) if antiguedades else None,
        "dias_promedio": round(sum(antiguedades) / len(antiguedades)) if antiguedades else None,
        "sin_texto": sum(1 for a in vivos if a.get("sin_texto")),
    }


def _antiguedad(fecha_inicio: str | None, fin: str) -> int | None:
    """Días que lleva al aire el mensaje, según la fecha que informa la fuente."""
    if not fecha_inicio:
        return None
    try:
        inicio = date.fromisoformat(str(fecha_inicio)[:10])
        hasta = date.fromisoformat(str(fin)[:10])
    except ValueError:
        return None
    return max((hasta - inicio).days, 0)


def totales(filas: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "competidores": len({f["competidor"] for f in filas}),
        "mensajes": sum(f["mensajes"] for f in filas),
        "piezas": sum(f["piezas"] for f in filas),
        "nuevos": sum(f["nuevos"] for f in filas),
        "cambiados": sum(f["cambiados"] for f in filas),
        "apagados": sum(f["apagados"] for f in filas),
    }


def resumir_para_prompt(filas: list[dict[str, Any]]) -> str:
    """Las señales en texto plano, para que el modelo las interprete sin
    tener que calcular nada (los modelos son malos contando)."""
    lineas = []
    for f in filas:
        partes = [
            f"{f['competidor']} [{f['plataforma']}]",
            f"{f['mensajes']} mensaje(s) al aire en {f['piezas']} pieza(s)",
            f"{f['nuevos']} nuevo(s), {f['cambiados']} cambiado(s), {f['apagados']} apagado(s)",
        ]
        if f["dias_mensaje_mas_viejo"] is not None:
            partes.append(f"el más viejo lleva {f['dias_mensaje_mas_viejo']} días")
        if f["dias_promedio"] is not None:
            partes.append(f"promedio {f['dias_promedio']} días al aire")
        if f["variantes_max"] > 1:
            partes.append(f"hasta {f['variantes_max']} variantes de un mismo mensaje")
        if f["formatos"]:
            partes.append("formatos: " + ", ".join(f"{k} x{v}" for k, v in f["formatos"].items()))
        if f["plataformas"]:
            partes.append("publica en: " + ", ".join(f["plataformas"]))
        if f["sin_texto"]:
            partes.append(f"{f['sin_texto']} sin texto publicado por la fuente")
        lineas.append("  - " + " · ".join(partes))
    return "\n".join(lineas)
