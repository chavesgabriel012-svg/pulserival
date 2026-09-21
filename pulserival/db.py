"""Acceso a la base de datos SQLite.

Todo el proyecto usa este módulo; no hay SQL suelto en otros archivos salvo
consultas de lectura muy específicas.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

ESQUEMA = Path(__file__).parent / "esquema.sql"


def conectar(ruta: Path | None = None) -> sqlite3.Connection:
    ruta = Path(ruta) if ruta else config.ruta_db()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# Columnas agregadas después de la primera versión del esquema.
# CREATE TABLE IF NOT EXISTS no toca una tabla que ya existe, así que una base
# creada antes necesita el ALTER. Se hace acá para que actualizar el código
# nunca requiera borrar la base ni perder el historial de anuncios.
MIGRACIONES = (
    ("clientes", "clave", "TEXT"),
    ("competidores_seguidos", "clave", "TEXT"),
)


def inicializar(ruta: Path | None = None) -> Path:
    """Crea las tablas si no existen y aplica las migraciones pendientes.
    Es seguro correrlo muchas veces."""
    destino = Path(ruta) if ruta else config.ruta_db()
    con = conectar(destino)
    with con:
        # Las migraciones van ANTES del esquema: el esquema crea un índice
        # sobre una columna nueva, y ese CREATE INDEX falla si la tabla ya
        # existe sin esa columna.
        _migrar(con)
        con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    con.close()
    return destino


def _migrar(con: sqlite3.Connection) -> list[str]:
    """Agrega columnas nuevas a tablas que ya existen. No hace nada en una
    base recién creada (el esquema ya las trae)."""
    aplicadas: list[str] = []
    for tabla, columna, tipo in MIGRACIONES:
        existe = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)
        ).fetchone()
        if not existe:
            continue
        columnas = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}
        if columna not in columnas:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
            aplicadas.append(f"{tabla}.{columna}")
    return aplicadas


@contextmanager
def sesion(ruta: Path | None = None) -> Iterator[sqlite3.Connection]:
    con = conectar(ruta)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── helpers genéricos ────────────────────────────────────────────────
def insertar(con: sqlite3.Connection, tabla: str, datos: dict[str, Any]) -> int:
    campos = ", ".join(datos)
    marcas = ", ".join("?" for _ in datos)
    cur = con.execute(f"INSERT INTO {tabla} ({campos}) VALUES ({marcas})", tuple(datos.values()))
    return int(cur.lastrowid)


def actualizar(con: sqlite3.Connection, tabla: str, id_: int, datos: dict[str, Any]) -> None:
    if not datos:
        return
    sets = ", ".join(f"{c} = ?" for c in datos)
    con.execute(f"UPDATE {tabla} SET {sets} WHERE id = ?", (*datos.values(), id_))


def filas(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(con.execute(sql, params))


def fila(con: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return con.execute(sql, params).fetchone()


def json_o_nada(valor: Any) -> str | None:
    return json.dumps(valor, ensure_ascii=False) if valor is not None else None


def leer_json(valor: Any, defecto: Any = None) -> Any:
    if not valor:
        return defecto
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return defecto


# ── consultas de negocio ─────────────────────────────────────────────
def clientes_activos(con: sqlite3.Connection, cliente_id: int | None = None) -> list[sqlite3.Row]:
    """Clientes activos. Con cliente_id, ese cliente *si* está activo:
    desactivar un cliente tiene que detener el gasto de scraper también
    cuando la corrida apunta a él por id."""
    if cliente_id:
        return filas(con, "SELECT * FROM clientes WHERE id = ? AND activo = 1", (cliente_id,))
    return filas(con, "SELECT * FROM clientes WHERE activo = 1 ORDER BY id")


def competidores_de(con: sqlite3.Connection, cliente_id: int) -> list[sqlite3.Row]:
    return filas(
        con,
        "SELECT * FROM competidores_seguidos WHERE cliente_id = ? AND activo = 1 "
        "ORDER BY prioridad, nombre",
        (cliente_id,),
    )


def registrar_uso_ia(con: sqlite3.Connection, **datos: Any) -> None:
    insertar(con, "uso_ia", datos)
