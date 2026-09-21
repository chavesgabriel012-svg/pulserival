"""config/clientes.yaml -> base de datos.

Sin esto, el servidor que corre el cron arranca con una base vacía: la base
no se versiona, así que la lista de clientes tiene que vivir en un archivo.
"""
from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from pulserival import db, sincronizar
from tests.base import CasoBase

BASE = """
clientes:
  - clave: piloto
    empresa: "Ferretería El Tornillo"
    contacto_email: "luis@tornillo.test"
    periodicidad: semanal
    industria: ferreterías
    competidores:
      - nombre: "Competidor A"
        meta_consulta: "Competidor A"
        prioridad: 1
      - nombre: "Competidor B"
        google_dominio: "b.test"
"""


class TestSincronizar(CasoBase):
    def escribir(self, texto: str) -> Path:
        ruta = Path(self.tmp.name) / "clientes.yaml"
        ruta.write_text(textwrap.dedent(texto), encoding="utf-8")
        return ruta

    def test_crea_cliente_y_competidores(self):
        resumen = sincronizar.aplicar(self.con, self.escribir(BASE))
        self.assertEqual(resumen["creados"], ["piloto"])
        self.assertEqual(len(resumen["competidores_creados"]), 2)
        cliente = db.fila(self.con, "SELECT * FROM clientes WHERE clave = 'piloto'")
        self.assertEqual(cliente["nombre_empresa"], "Ferretería El Tornillo")
        self.assertEqual(len(db.competidores_de(self.con, int(cliente["id"]))), 2)

    def test_es_idempotente(self):
        ruta = self.escribir(BASE)
        sincronizar.aplicar(self.con, ruta)
        resumen = sincronizar.aplicar(self.con, ruta)
        self.assertEqual(resumen["creados"], [])
        self.assertEqual(resumen["competidores_creados"], [])
        self.assertEqual(db.fila(self.con, "SELECT COUNT(*) AS n FROM clientes")["n"], 1)
        self.assertEqual(
            db.fila(self.con, "SELECT COUNT(*) AS n FROM competidores_seguidos")["n"], 2)

    def test_actualiza_solo_lo_que_cambio(self):
        sincronizar.aplicar(self.con, self.escribir(BASE))
        resumen = sincronizar.aplicar(
            self.con, self.escribir(BASE.replace("periodicidad: semanal", "periodicidad: mensual")))
        self.assertEqual(resumen["actualizados"], ["piloto (periodicidad)"])

    def test_sacar_un_competidor_lo_desactiva_pero_no_borra_su_historial(self):
        ruta = self.escribir(BASE)
        sincronizar.aplicar(self.con, ruta)
        comp = db.fila(self.con, "SELECT * FROM competidores_seguidos WHERE clave = 'Competidor B'")
        db.insertar(self.con, "anuncios_detectados", {
            "competidor_id": comp["id"], "plataforma": "google", "fuente": "test",
            "huella": "h1", "titulo": "Un anuncio ya detectado"})
        self.con.commit()

        sin_b = BASE.split("      - nombre: \"Competidor B\"")[0]
        resumen = sincronizar.aplicar(self.con, self.escribir(sin_b))
        self.assertEqual(resumen["competidores_desactivados"], ["piloto/Competidor B"])
        fila = db.fila(self.con, "SELECT * FROM competidores_seguidos WHERE clave = 'Competidor B'")
        self.assertEqual(fila["activo"], 0, "se desactiva")
        self.assertEqual(db.fila(self.con, "SELECT COUNT(*) AS n FROM anuncios_detectados")["n"], 1,
                         "borrarlo se llevaría el historial que permite decir qué es nuevo")

    def test_sacar_un_cliente_lo_desactiva(self):
        sincronizar.aplicar(self.con, self.escribir(BASE))
        resumen = sincronizar.aplicar(self.con, self.escribir("clientes: []"))
        self.assertEqual(resumen["desactivados"], ["piloto"])
        self.assertEqual(db.clientes_activos(self.con), [])

    def test_rechaza_configuracion_invalida(self):
        casos = {
            "sin clave": "clientes:\n  - empresa: X\n    contacto_email: a@b.c\n",
            "sin correo": "clientes:\n  - clave: x\n    empresa: X\n",
            "periodicidad rara": ("clientes:\n  - clave: x\n    empresa: X\n"
                                  "    contacto_email: a@b.c\n    periodicidad: diaria\n"),
            "competidor sin fuente": ("clientes:\n  - clave: x\n    empresa: X\n"
                                      "    contacto_email: a@b.c\n    competidores:\n"
                                      "      - nombre: Solo un nombre\n"),
            "clave repetida": ("clientes:\n  - clave: x\n    empresa: X\n    contacto_email: a@b.c\n"
                               "  - clave: x\n    empresa: Y\n    contacto_email: c@d.e\n"),
        }
        for etiqueta, texto in casos.items():
            with self.subTest(etiqueta):
                with self.assertRaises(sincronizar.ConfigInvalida):
                    sincronizar.aplicar(self.con, self.escribir(texto))

    def test_el_archivo_real_del_repo_es_valido(self):
        """El config/clientes.yaml que está en el repo tiene que poder aplicarse:
        si no, el cron falla el lunes a las 5 a.m."""
        resumen = sincronizar.aplicar(self.con, None)
        self.assertTrue(resumen["creados"], "el archivo del repo no creó ningún cliente")
        for cliente in db.clientes_activos(self.con):
            self.assertTrue(db.competidores_de(self.con, int(cliente["id"])),
                            f"{cliente['nombre_empresa']} no tiene competidores")

    def test_la_entrada_de_prueba_no_le_escribe_a_nadie(self):
        """La entrada de prueba del repo usa un dominio .invalid a propósito:
        un envío accidental rebota en vez de llegarle a una persona."""
        sincronizar.aplicar(self.con, None)
        for cliente in db.clientes_activos(self.con):
            if "PRUEBA" in (cliente["nombre_empresa"] or ""):
                self.assertTrue(cliente["contacto_email"].endswith(".invalid"))


class TestMigracion(unittest.TestCase):
    def test_una_base_vieja_se_migra_sin_perder_datos(self):
        """Agregar una columna no puede obligar a borrar la base: ahí vive el
        historial que permite decir 'este anuncio es nuevo'."""
        import sqlite3
        import tempfile

        ruta = Path(tempfile.mkdtemp()) / "vieja.db"
        esquema = (Path(__file__).parent.parent / "pulserival" / "esquema.sql").read_text()
        viejo = (esquema
                 .replace("    clave             TEXT    UNIQUE,"
                          "      -- id estable para config/clientes.yaml\n", "")
                 .replace("    clave                  TEXT,"
                          "                  -- id estable dentro del cliente\n", "")
                 .replace("CREATE UNIQUE INDEX IF NOT EXISTS idx_competidores_clave\n"
                          "    ON competidores_seguidos(cliente_id, clave) WHERE clave IS NOT NULL;", ""))
        con = sqlite3.connect(ruta)
        con.executescript(viejo)
        con.execute("INSERT INTO clientes (nombre_empresa, contacto_email) VALUES ('Viejo SA', 'a@b.c')")
        con.commit()
        con.close()

        db.inicializar(ruta)
        db.inicializar(ruta)   # dos veces: tiene que ser idempotente
        con = sqlite3.connect(ruta)
        columnas = [r[1] for r in con.execute("PRAGMA table_info(clientes)")]
        self.assertIn("clave", columnas)
        self.assertEqual(con.execute("SELECT nombre_empresa FROM clientes").fetchone()[0], "Viejo SA")
        con.close()
