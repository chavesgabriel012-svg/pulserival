"""Tareas de mantenimiento sobre datos ya guardados.

Cuando cambia la forma de calcular la huella de un anuncio, las filas viejas
quedan con la huella anterior. Sin recalcularlas, la corrida siguiente ve
huellas distintas para los mismos anuncios y reporta un cambio masivo que no
ocurrió: exactamente el problema que el arreglo venía a resolver.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import db, util
from .fuentes.base import AnuncioCrudo


def recalcular_huellas(con: sqlite3.Connection, aplicar: bool = True) -> dict[str, Any]:
    """Recalcula la huella de cada anuncio a partir de sus campos guardados.

    Si dos filas del mismo competidor terminan con la misma huella —porque
    eran el mismo anuncio contado dos veces por culpa de la URL firmada— se
    conserva la más antigua y se descarta la duplicada, para que el historial
    quede como debió estar.
    """
    filas = db.filas(con, "SELECT * FROM anuncios_detectados ORDER BY id")
    vistas: dict[tuple, int] = {}
    cambiadas = duplicadas = 0

    for f in filas:
        crudo = AnuncioCrudo(
            plataforma=f["plataforma"], fuente=f["fuente"], id_externo=f["id_externo"],
            titulo=f["titulo"], texto=f["texto"], descripcion=f["descripcion"],
            cta=f["cta"], link_destino=f["link_destino"], creativo_url=f["creativo_url"],
        )
        nueva = crudo.huella()
        clave = (f["competidor_id"], f["plataforma"], nueva)

        if clave in vistas:
            duplicadas += 1
            if aplicar:
                # La fila vieja se queda; esta era el mismo anuncio recontado.
                con.execute("DELETE FROM anuncios_en_reporte WHERE anuncio_id = ?", (f["id"],))
                con.execute("DELETE FROM anuncios_detectados WHERE id = ?", (f["id"],))
                con.execute(
                    "UPDATE anuncios_detectados SET visto_ultimo_en = ?, estado = 'activo' "
                    "WHERE id = ?", (util.ahora_iso(), vistas[clave]))
            continue

        vistas[clave] = int(f["id"])
        if nueva != f["huella"]:
            cambiadas += 1
            if aplicar:
                db.actualizar(con, "anuncios_detectados", int(f["id"]), {"huella": nueva})

    return {"revisados": len(filas), "huellas_actualizadas": cambiadas,
            "duplicados_fusionados": duplicadas, "aplicado": aplicar}
