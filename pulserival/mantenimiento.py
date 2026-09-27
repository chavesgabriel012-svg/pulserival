"""Tareas de mantenimiento sobre datos ya guardados.

Cuando cambia la forma de calcular la huella de un anuncio, las filas viejas
quedan con la huella anterior. Sin recalcularlas, la corrida siguiente ve
huellas distintas para los mismos anuncios y reporta un cambio masivo que no
ocurrió: exactamente el problema que el arreglo venía a resolver.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, db, util
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


def respaldar(ruta_db: Path | None = None, destino: Path | None = None,
              conservar: int = 7) -> dict[str, Any]:
    """Una copia consistente de la base, sin detener el servidor.

    Usa la API de respaldo de SQLite y no `cp`: copiar el archivo mientras hay
    alguien escribiendo puede dejar una copia rota que además parece sana, y no
    se descubre hasta que hace falta restaurarla. `Connection.backup()` toma una
    instantánea coherente aunque el servidor esté trabajando.

    Existe porque los respaldos automáticos del volumen en Railway son solo del
    plan Pro. **Esto NO reemplaza un respaldo fuera del servidor**: vive en el
    mismo disco, así que cubre una corrupción, una migración mala o un borrado
    por error, pero no que se pierda el disco. Para eso hay que bajarse una
    copia (ver docs/11-servidor.md).
    """
    origen = Path(ruta_db) if ruta_db else config.ruta_db()
    if not origen.exists():
        raise FileNotFoundError(f"No existe la base {origen}")
    carpeta = Path(destino) if destino else (config.DIR_DATOS / "respaldos")
    carpeta.mkdir(parents=True, exist_ok=True)

    marca = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archivo = carpeta / f"pulserival-{marca}.db"
    con_origen = sqlite3.connect(origen)
    try:
        con_destino = sqlite3.connect(archivo)
        try:
            con_origen.backup(con_destino)
        finally:
            con_destino.close()
    finally:
        con_origen.close()

    # Rotación: se borran los más viejos. El nombre lleva la fecha en formato
    # ordenable, así que alfabético y cronológico son lo mismo.
    previos = sorted(carpeta.glob("pulserival-*.db"))
    borrados = []
    for viejo in previos[:-conservar] if conservar > 0 else []:
        viejo.unlink()
        borrados.append(viejo.name)

    return {"archivo": str(archivo), "bytes": archivo.stat().st_size,
            "conservados": len(previos) - len(borrados), "borrados": borrados}
