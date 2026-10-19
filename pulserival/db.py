"""Acceso a la base de datos SQLite.

Todo el proyecto usa este módulo; no hay SQL suelto en otros archivos salvo
consultas de lectura muy específicas.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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
    # Planes y suscripción. Se agregan como columnas nuevas, sin tocar el
    # CHECK de `periodicidad`: SQLite no sabe modificar un CHECK sin
    # reconstruir la tabla entera, y reconstruirla se llevaría por delante
    # las claves foráneas de los reportes y anuncios ya guardados.
    # `cadencia_dias` es lo que permite cadencias arbitrarias sin ampliar
    # ese enum.
    ("clientes", "plan", "TEXT"),
    ("clientes", "estado_suscripcion", "TEXT"),
    ("clientes", "cadencia_dias", "INTEGER"),
    ("clientes", "precio_mensual_usd", "REAL"),
    ("clientes", "pago_proveedor", "TEXT"),
    ("clientes", "pago_referencia", "TEXT"),
    # De dónde salió el cliente, según la encuesta del formulario de alta.
    ("clientes", "origen", "TEXT"),
    # Por qué se descartó un reporte. Sin esto, `estado = 'descartado'` no dice
    # nada dentro de seis meses, y es justamente el dato que explica qué tuvo
    # de malo el borrador: el insumo de la Fase 2 para los descartes, igual que
    # el diff lo es para las ediciones.
    ("reportes_generados", "motivo_descarte", "TEXT"),
)

# Qué poner en las filas que ya existían cuando se agrega una columna.
# Solo corre en la misma transacción en que la columna se crea: un cliente
# que después cambie de plan a mano no se pisa en la corrida siguiente.
RELLENOS = {
    # Un cliente que ya existía estaba ahí porque alguien lo cargó y le está
    # reportando: su plan es la cadencia que ya tenía, y su suscripción está
    # activa. Marcarlo como 'prueba' le cortaría los reportes.
    "clientes.plan": "UPDATE clientes SET plan = periodicidad WHERE plan IS NULL",
    "clientes.estado_suscripcion":
        "UPDATE clientes SET estado_suscripcion = 'activa' WHERE estado_suscripcion IS NULL",
}


# La versión del esquema, en `PRAGMA user_version`. Es informativa: lo que
# decide si hay que reconstruir `clientes` es mirar su CHECK de verdad (ver
# `_clientes_al_dia`), no confiar en un número que alguien pudo no subir.
VERSION_ESQUEMA = 2


def inicializar(ruta: Path | None = None) -> Path:
    """Crea las tablas si no existen y aplica las migraciones pendientes.
    Es seguro correrlo muchas veces."""
    destino = Path(ruta) if ruta else config.ruta_db()
    migrar(destino)
    return destino


def migrar(ruta: Path | None = None) -> dict[str, Any]:
    """Lleva la base al esquema actual y cuenta qué hizo.

    Tres pasos, en este orden y por una razón cada uno:
      1. columnas nuevas por ALTER TABLE (MIGRACIONES), con sus rellenos;
      2. reconstruir `clientes` si su CHECK de periodicidad es el viejo, que
         es lo único que un ALTER TABLE de SQLite no sabe cambiar;
      3. el esquema completo, que crea las tablas e índices que falten.
    El 2 va antes del 3 porque el esquema crea índices sobre columnas que
    solo existen después de reconstruir.
    """
    destino = Path(ruta) if ruta else config.ruta_db()
    informe: dict[str, Any] = {"ruta": str(destino), "columnas": [], "clientes": None}
    con = conectar(destino)
    try:
        with con:
            aplicadas = _migrar(con)
            for columna in aplicadas:
                relleno = RELLENOS.get(columna)
                if relleno:
                    con.execute(relleno)
        informe["columnas"] = aplicadas
        informe["clientes"] = _reconstruir_clientes(con, destino)
        with con:
            con.executescript(ESQUEMA.read_text(encoding="utf-8"))
            con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA}")
    finally:
        con.close()
    return informe


def _ddl_clientes() -> str:
    """El CREATE TABLE de `clientes`, sacado de esquema.sql.

    De ahí y no copiado acá: la tabla reconstruida tiene que ser idéntica a
    la de una base nueva, y la única forma de garantizarlo es que salgan del
    mismo texto.
    """
    texto = ESQUEMA.read_text(encoding="utf-8")
    hallado = re.search(r"CREATE TABLE IF NOT EXISTS clientes \(.*?\n\);", texto, re.S)
    if not hallado:
        raise RuntimeError("esquema.sql no tiene el CREATE TABLE de clientes")
    return hallado.group(0)


def _clientes_al_dia(con: sqlite3.Connection) -> bool | None:
    """¿La tabla ya acepta 'custom'? None si la tabla no existe todavía."""
    fila_ = con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'clientes'"
    ).fetchone()
    if fila_ is None:
        return None
    return "'custom'" in (fila_[0] or "")


def _reconstruir_clientes(con: sqlite3.Connection, ruta: Path) -> dict[str, Any] | None:
    """Reconstruye `clientes` con el CHECK nuevo. No hace nada si ya está.

    SQLite no permite modificar un CHECK: hay que crear la tabla de nuevo,
    copiar las filas, borrar la vieja y renombrar la nueva. Es el
    procedimiento de 12 pasos de la documentación de SQLite
    (sqlite.org/lang_altertable.html#otheralter), y cada detalle de abajo
    está porque saltearlo rompe algo de forma silenciosa:

      - Las claves foráneas se apagan ANTES de abrir la transacción. Las
        tablas hijas (competidores, reportes, feedback) tienen ON DELETE
        CASCADE: con las claves encendidas, el DROP TABLE de la tabla vieja
        borraría en cascada todo el historial. Y el PRAGMA no tiene efecto
        dentro de una transacción, así que el orden importa.
      - Se crea la nueva, se copia y recién después se borra la vieja y se
        renombra la nueva. Al revés (renombrar la vieja primero), SQLite
        reescribe las claves foráneas de las hijas para que apunten a la
        tabla renombrada, y quedan colgando de una tabla que después se borra.
      - Las columnas se copian por nombre y no por posición: en una base
        vieja las columnas agregadas por ALTER quedaron al final, en otro
        orden que el del esquema.
      - Se conserva el contador de AUTOINCREMENT. Copiar las filas lo deja en
        el id más alto que quedó; si se habían borrado clientes del final, el
        próximo alta reusaría el id de uno borrado, y Paddle guarda ids de
        cliente en `custom_data`.
      - Antes de confirmar se corre `foreign_key_check`: si algo quedó
        colgando, se deshace todo y la base queda como estaba.

    Antes de tocar nada se hace una copia de la base con la API de respaldo
    (no `cp`, que copia a medias si hay alguien escribiendo).
    """
    al_dia = _clientes_al_dia(con)
    if al_dia is None or al_dia:
        return None

    respaldo = _respaldar_antes_de_migrar(ruta)
    viejas = [f[1] for f in con.execute("PRAGMA table_info(clientes)")]
    nueva = _ddl_clientes().replace("CREATE TABLE IF NOT EXISTS clientes",
                                    "CREATE TABLE clientes_nueva", 1)

    # Qué filas pasan a 'custom'. Un cliente "a la medida" era plan 'custom'
    # con periodicidad 'semanal' y la cadencia en `cadencia_dias`, porque el
    # CHECK viejo no dejaba escribir otra cosa. Ahora se dice tal cual.
    # También pasa a 'custom' cualquier fila cuya cadencia no coincida con su
    # periodicidad: ese cliente ya se estaba reportando cada N días.
    # Una cadencia igual a la de su periodicidad (7 en semanal, 30 en
    # mensual) se borra: repite lo mismo y el CHECK nuevo no la admite.
    tiene_cadencia = "cadencia_dias" in viejas
    tiene_plan = "plan" in viejas
    if tiene_cadencia:
        es_custom = ("(cadencia_dias IS NOT NULL AND ("
                     + ("plan = 'custom' OR " if tiene_plan else "")
                     + "cadencia_dias <> (CASE periodicidad WHEN 'semanal' THEN 7 "
                       "WHEN 'mensual' THEN 30 ELSE -1 END)))")
    else:
        es_custom = "0"
    expresiones = {
        "periodicidad": f"CASE WHEN {es_custom} THEN 'custom' ELSE periodicidad END",
        "cadencia_dias": f"CASE WHEN {es_custom} THEN cadencia_dias ELSE NULL END",
    }

    nuevas_cols = None
    filas_despues = 0
    convertidos: list[dict[str, Any]] = []
    sin_cadencia: list[dict[str, Any]] = []
    con.commit()
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("BEGIN IMMEDIATE")
        if tiene_cadencia:
            convertidos = [dict(f) for f in con.execute(
                f"SELECT id, nombre_empresa, periodicidad, cadencia_dias FROM clientes "
                f"WHERE {es_custom}")]
        if tiene_plan:
            sin_cadencia = [dict(f) for f in con.execute(
                "SELECT id, nombre_empresa FROM clientes WHERE plan = 'custom' AND "
                + ("cadencia_dias IS NULL" if tiene_cadencia else "1"))]
        secuencia = con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'clientes'").fetchone()
        filas_antes = con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]

        con.execute(nueva)
        nuevas_cols = [f[1] for f in con.execute("PRAGMA table_info(clientes_nueva)")]
        comunes = [c for c in nuevas_cols if c in viejas]
        destino_cols = ", ".join(comunes)
        origen_cols = ", ".join(expresiones.get(c, c) for c in comunes)
        con.execute(f"INSERT INTO clientes_nueva ({destino_cols}) "
                    f"SELECT {origen_cols} FROM clientes")
        con.execute("DROP TABLE clientes")
        con.execute("ALTER TABLE clientes_nueva RENAME TO clientes")
        if secuencia is not None:
            # El RENAME se lleva la fila de sqlite_sequence de clientes_nueva;
            # acá se le devuelve el valor que tenía la tabla vieja si era mayor.
            con.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'clientes'",
                        (secuencia[0],))
            if con.execute("SELECT changes()").fetchone()[0] == 0:
                con.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('clientes', ?)",
                            (secuencia[0],))
        filas_despues = con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
        if filas_despues != filas_antes:
            raise sqlite3.IntegrityError(
                f"clientes tenía {filas_antes} filas y quedó con {filas_despues}; "
                "no se aplicó nada.")
        colgando = con.execute("PRAGMA foreign_key_check").fetchall()
        if colgando:
            raise sqlite3.IntegrityError(
                f"Quedaron {len(colgando)} filas con claves foráneas rotas tras "
                f"reconstruir clientes; no se aplicó nada. Primera: {tuple(colgando[0])}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON")

    return {
        "respaldo": str(respaldo),
        # Comprobado antes del COMMIT: si no coincidían, no se aplicó nada.
        "filas": filas_despues,
        "columnas_nuevas": [c for c in (nuevas_cols or []) if c not in viejas],
        "pasados_a_custom": convertidos,
        # Plan 'custom' sin cadencia: no se puede inventar una, así que siguen
        # con su periodicidad de siempre. Hay que cargarles la cadencia a mano.
        "custom_sin_cadencia": sin_cadencia,
    }


def _respaldar_antes_de_migrar(ruta: Path) -> Path:
    """Copia de la base antes de reconstruir. Va a su propia carpeta para
    que la rotación de los respaldos diarios (que se quedan con los últimos
    siete) no se la lleve: si la migración salió mal, se nota días después."""
    carpeta = Path(ruta).parent / "respaldos" / "migraciones"
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archivo = carpeta / f"antes-de-migrar-{marca}.db"
    origen = sqlite3.connect(ruta)
    try:
        copia = sqlite3.connect(archivo)
        try:
            origen.backup(copia)
        finally:
            copia.close()
    finally:
        origen.close()
    return archivo


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


def valor(fila: Any, columna: str, defecto: Any = None) -> Any:
    """Lee una columna que puede no existir todavía en esta base.

    Las columnas nuevas entran por MIGRACIONES, que solo corre en
    `inicializar()`. El cron siempre lo llama, pero alguien puede correr un
    comando suelto contra una base vieja copiada de otro lado; sin esto, eso
    revienta con un KeyError en vez de seguir con el valor por defecto.
    """
    if fila is None:
        return defecto
    try:
        claves = fila.keys()
    except AttributeError:
        return (fila or {}).get(columna, defecto)
    if columna not in claves:
        return defecto
    leido = fila[columna]
    return defecto if leido is None else leido


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
