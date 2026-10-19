"""La reconstrucción de `clientes` para el CHECK nuevo de periodicidad.

SQLite no sabe cambiar un CHECK: hay que crear la tabla otra vez, copiar las
filas, borrar la vieja y renombrar la nueva. Cada test de acá cuida un modo en
que ese procedimiento rompe datos sin avisar. El más caro es el primero: las
tablas hijas tienen ON DELETE CASCADE, y un DROP TABLE con las claves foráneas
encendidas se lleva todo el historial de anuncios y reportes.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pulserival import db

# Así era `clientes` antes de los planes. La base de la rama `datos` todavía
# tiene esta forma, y la de Railway la tiene con las columnas de los planes
# agregadas por ALTER al final (que es lo que hace `_migrar` en `_vieja`).
CLIENTES_VIEJA = """CREATE TABLE IF NOT EXISTS clientes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    clave             TEXT    UNIQUE,
    nombre_empresa    TEXT    NOT NULL,
    contacto_nombre   TEXT,
    contacto_email    TEXT    NOT NULL,
    contacto_whatsapp TEXT,
    periodicidad      TEXT    NOT NULL DEFAULT 'semanal'
                              CHECK (periodicidad IN ('semanal','mensual')),
    dia_envio         TEXT    DEFAULT 'martes',
    industria         TEXT,
    notas             TEXT,
    activo            INTEGER NOT NULL DEFAULT 1,
    creado_en         TEXT    NOT NULL DEFAULT (datetime('now'))
);"""


class CasoMigracion(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = Path(self.tmp.name) / "pulserival.db"

    def _vieja(self) -> sqlite3.Connection:
        """Una base con el `clientes` viejo y el resto de las tablas actuales."""
        esquema = db.ESQUEMA.read_text(encoding="utf-8")
        esquema = esquema.replace(db._ddl_clientes(), CLIENTES_VIEJA)
        con = sqlite3.connect(self.ruta)
        con.row_factory = sqlite3.Row
        con.executescript(esquema)
        # Las columnas de los planes, agregadas al final como en producción.
        db._migrar(con)
        con.commit()
        return con

    def _cliente(self, con, **datos) -> int:
        base = {"nombre_empresa": "Ferretería El Roble", "contacto_email": "a@b.test"}
        base.update(datos)
        cur = con.execute(
            f"INSERT INTO clientes ({', '.join(base)}) VALUES ({', '.join('?' * len(base))})",
            tuple(base.values()))
        con.commit()
        return int(cur.lastrowid)

    def _abrir(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(con.close)
        return con


class TestNoSePierdeNada(CasoMigracion):
    def test_el_drop_no_borra_en_cascada_el_historial(self):
        # Si el PRAGMA foreign_keys = OFF quedara dentro de la transacción (no
        # tiene efecto ahí) o faltara, el DROP TABLE de la tabla vieja borraría
        # en cascada competidores, reportes y todo lo que cuelga de ellos.
        con = self._vieja()
        cid = self._cliente(con)
        con.execute("PRAGMA foreign_keys = ON")
        comp = con.execute(
            "INSERT INTO competidores_seguidos (cliente_id, nombre) VALUES (?, 'EPA')",
            (cid,)).lastrowid
        con.execute("INSERT INTO reportes_generados (cliente_id, periodo_inicio, periodo_fin) "
                    "VALUES (?, '2026-09-01', '2026-09-30')", (cid,))
        con.execute("INSERT INTO anuncios_detectados (competidor_id, plataforma, fuente, "
                    "id_externo, huella) VALUES (?, 'meta', 'test', 'X1', 'h1')", (comp,))
        con.commit()
        con.close()

        db.migrar(self.ruta)

        con = self._abrir()
        for tabla in ("clientes", "competidores_seguidos", "reportes_generados",
                      "anuncios_detectados"):
            with self.subTest(tabla=tabla):
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0], 1)

    def test_las_hijas_siguen_apuntando_a_clientes(self):
        # Renombrar la vieja antes de crear la nueva hace que SQLite reescriba
        # las claves foráneas de las hijas hacia el nombre viejo, y quedan
        # colgando de una tabla que después se borra.
        con = self._vieja()
        con.close()
        db.migrar(self.ruta)
        con = self._abrir()
        for hija in ("competidores_seguidos", "reportes_generados", "feedback_cliente"):
            with self.subTest(hija=hija):
                destinos = {f[2] for f in con.execute(f"PRAGMA foreign_key_list({hija})")}
                self.assertIn("clientes", destinos)
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'clientes_nueva'").fetchone()[0], 0)

    def test_conserva_las_columnas_aunque_esten_en_otro_orden(self):
        # En una base vieja las columnas de los planes quedaron al final. Una
        # copia por posición metería el plan en `dia_envio` y así.
        con = self._vieja()
        cid = self._cliente(con, plan="mensual", estado_suscripcion="activa",
                            dia_envio="jueves", industria="ferreterías",
                            precio_mensual_usd=20.0, origen="Google")
        con.close()
        db.migrar(self.ruta)
        fila = self._abrir().execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        self.assertEqual(fila["plan"], "mensual")
        self.assertEqual(fila["dia_envio"], "jueves")
        self.assertEqual(fila["industria"], "ferreterías")
        self.assertEqual(fila["precio_mensual_usd"], 20.0)
        self.assertEqual(fila["origen"], "Google")

    def test_el_proximo_cliente_no_reusa_el_id_de_uno_borrado(self):
        # Copiar las filas deja AUTOINCREMENT en el id más alto que quedó. Si
        # se habían borrado los últimos, el próximo alta tomaría el id de uno
        # borrado, y Paddle guarda ids de cliente en custom_data.
        con = self._vieja()
        for _ in range(5):
            self._cliente(con)
        con.execute("DELETE FROM clientes WHERE id IN (4, 5)")
        con.commit()
        con.close()
        db.migrar(self.ruta)
        con = self._abrir()
        nuevo = self._cliente(con)
        self.assertEqual(nuevo, 6)


class TestElCheckNuevo(CasoMigracion):
    def test_acepta_custom_con_cadencia(self):
        con = self._vieja()
        con.close()
        db.migrar(self.ruta)
        con = self._abrir()
        cid = self._cliente(con, periodicidad="custom", cadencia_dias=2)
        self.assertEqual(con.execute("SELECT periodicidad FROM clientes WHERE id = ?",
                                     (cid,)).fetchone()[0], "custom")

    def test_rechaza_custom_sin_cadencia(self):
        db.inicializar(self.ruta)
        con = self._abrir()
        with self.assertRaises(sqlite3.IntegrityError):
            self._cliente(con, periodicidad="custom")

    def test_rechaza_cadencia_fuera_de_custom(self):
        # Dos lugares diciendo cada cuánto es la forma de que digan cosas
        # distintas.
        db.inicializar(self.ruta)
        con = self._abrir()
        with self.assertRaises(sqlite3.IntegrityError):
            self._cliente(con, periodicidad="semanal", cadencia_dias=3)

    def test_rechaza_cadencia_cero(self):
        db.inicializar(self.ruta)
        con = self._abrir()
        with self.assertRaises(sqlite3.IntegrityError):
            self._cliente(con, periodicidad="custom", cadencia_dias=0)

    def test_trae_las_columnas_de_paddle_y_la_tabla_de_eventos(self):
        con = self._vieja()
        con.close()
        db.migrar(self.ruta)
        con = self._abrir()
        columnas = {f[1] for f in con.execute("PRAGMA table_info(clientes)")}
        for c in ("ciclo_facturacion", "paddle_suscripcion_id", "paddle_cliente_id",
                  "paddle_transaccion_id", "paddle_estado", "paddle_actualizado_en"):
            self.assertIn(c, columnas)
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'eventos_pago'").fetchone()[0], 1)


class TestComoSeTraducenLosClientesDeAntes(CasoMigracion):
    def test_un_cliente_a_la_medida_pasa_a_custom(self):
        # Antes un "a la medida" era semanal con la cadencia aparte, porque el
        # CHECK viejo no dejaba escribir otra cosa.
        con = self._vieja()
        cid = self._cliente(con, plan="custom", periodicidad="semanal", cadencia_dias=2)
        con.close()
        informe = db.migrar(self.ruta)
        fila = self._abrir().execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        self.assertEqual(fila["periodicidad"], "custom")
        self.assertEqual(fila["cadencia_dias"], 2)
        self.assertEqual([c["id"] for c in informe["clientes"]["pasados_a_custom"]], [cid])

    def test_una_cadencia_igual_a_la_periodicidad_se_borra(self):
        # Repite lo mismo, y el CHECK nuevo no admite cadencia fuera de custom.
        con = self._vieja()
        cid = self._cliente(con, plan="mensual", periodicidad="mensual", cadencia_dias=30)
        con.close()
        db.migrar(self.ruta)
        fila = self._abrir().execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        self.assertEqual(fila["periodicidad"], "mensual")
        self.assertIsNone(fila["cadencia_dias"])

    def test_una_cadencia_distinta_a_la_periodicidad_es_custom(self):
        # Ese cliente ya se estaba reportando cada 3 días: decirlo es la única
        # traducción que no le cambia nada.
        con = self._vieja()
        cid = self._cliente(con, plan="semanal", periodicidad="semanal", cadencia_dias=3)
        con.close()
        db.migrar(self.ruta)
        fila = self._abrir().execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        self.assertEqual(fila["periodicidad"], "custom")
        self.assertEqual(fila["cadencia_dias"], 3)

    def test_a_la_medida_sin_cadencia_no_se_inventa_una(self):
        # Se queda con su periodicidad de siempre y se avisa: cargarle una
        # cadencia es una decisión de negocio, no de una migración.
        con = self._vieja()
        cid = self._cliente(con, plan="custom", periodicidad="semanal")
        con.close()
        informe = db.migrar(self.ruta)
        fila = self._abrir().execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
        self.assertEqual(fila["periodicidad"], "semanal")
        self.assertEqual([c["id"] for c in informe["clientes"]["custom_sin_cadencia"]], [cid])


class TestSeguridadDeLaMigracion(CasoMigracion):
    def test_deja_un_respaldo_con_la_base_de_antes(self):
        con = self._vieja()
        self._cliente(con)
        con.close()
        informe = db.migrar(self.ruta)
        respaldo = Path(informe["clientes"]["respaldo"])
        self.assertTrue(respaldo.exists())
        copia = sqlite3.connect(respaldo)
        self.addCleanup(copia.close)
        ddl = copia.execute("SELECT sql FROM sqlite_master WHERE name = 'clientes'").fetchone()[0]
        self.assertNotIn("'custom'", ddl, "el respaldo es la base de ANTES de migrar")
        self.assertEqual(copia.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
        # Fuera de la carpeta que rota: la rotación diaria se queda con siete.
        self.assertEqual(respaldo.parent.name, "migraciones")

    def test_correrla_dos_veces_no_hace_nada_la_segunda(self):
        con = self._vieja()
        con.close()
        primera = db.migrar(self.ruta)
        segunda = db.migrar(self.ruta)
        self.assertIsNotNone(primera["clientes"])
        self.assertIsNone(segunda["clientes"])
        respaldos = list((self.ruta.parent / "respaldos" / "migraciones").glob("*.db"))
        self.assertEqual(len(respaldos), 1)

    def test_si_algo_queda_colgando_se_deshace_todo(self):
        # Una fila hija huérfana (de una base vieja sin claves foráneas
        # encendidas) hace fallar el foreign_key_check: la migración no se
        # aplica a medias, la tabla vieja queda como estaba.
        con = self._vieja()
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("INSERT INTO competidores_seguidos (cliente_id, nombre) VALUES (999, 'X')")
        con.commit()
        con.close()
        with self.assertRaises(sqlite3.IntegrityError):
            db.migrar(self.ruta)
        con = self._abrir()
        ddl = con.execute("SELECT sql FROM sqlite_master WHERE name = 'clientes'").fetchone()[0]
        self.assertNotIn("'custom'", ddl)
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'clientes_nueva'").fetchone()[0], 0)

    def test_una_base_nueva_no_se_reconstruye(self):
        informe = db.migrar(self.ruta)
        self.assertIsNone(informe["clientes"])
        con = self._abrir()
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], db.VERSION_ESQUEMA)
        self.assertFalse((self.ruta.parent / "respaldos").exists())


if __name__ == "__main__":
    unittest.main()
