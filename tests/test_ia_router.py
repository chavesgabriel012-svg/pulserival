"""Router de IA: intercambiable, con estimación de costo y tope de gasto."""
from __future__ import annotations

import unittest

from pulserival.ia import Peticion, Presupuesto, PresupuestoExcedido, ejecutar
from pulserival.ia import prompts
from pulserival.ia.base import extraer_json
from pulserival.ia.router import candidatos, costo


class TestRouter(unittest.TestCase):
    def test_config_define_los_modelos_no_el_codigo(self):
        for tarea in ("analizar_anuncio", "redactar_reporte", "etiquetar_edicion"):
            lista = candidatos(tarea)
            self.assertTrue(lista, f"{tarea} sin modelos en config/modelos.yaml")
            self.assertEqual(lista[-1]["proveedor"], "stub",
                             "la última opción debe ser el modo sin IA: el pipeline nunca se cae")

    def test_cae_al_stub_cuando_no_hay_claves(self):
        r = ejecutar(Peticion(tarea="analizar_anuncio", sistema="s", usuario="u",
                              datos={"titulo": "Matrícula gratis ¢19.900"}))
        self.assertEqual(r.proveedor, "stub")
        self.assertEqual(r.costo_usd, 0.0)
        datos = r.json()
        self.assertEqual(datos["tipo_oferta"], "promocion")
        self.assertEqual(datos["precios_mencionados"], ["¢19.900"])

    def test_calculo_de_costo(self):
        self.assertAlmostEqual(costo("gemini", "gemini-2.5-pro", 1_000_000, 0), 1.25, places=4)
        self.assertAlmostEqual(costo("groq", "llama-3.1-8b-instant", 0, 1_000_000), 0.08, places=4)
        self.assertEqual(costo("proveedor-inexistente", "x", 1000, 1000), 0.0)

    def test_tope_de_gasto_corta(self):
        p = Presupuesto(tope_usd=0.01, gastado_usd=0.02)
        with self.assertRaises(PresupuestoExcedido):
            ejecutar(Peticion(tarea="analizar_anuncio", sistema="s", usuario="u"), presupuesto=p)

    def test_extrae_json_aunque_venga_en_bloque(self):
        self.assertEqual(extraer_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extraer_json('bla bla {"a": 2} fin'), {"a": 2})
        self.assertIsNone(extraer_json("no hay json"))


class TestPrompts(unittest.TestCase):
    def test_prompts_tienen_las_dos_secciones(self):
        for tarea in ("analizar_anuncio", "redactar_reporte", "etiquetar_edicion"):
            sistema, usuario = prompts.cargar(tarea)
            self.assertTrue(sistema.strip())
            self.assertTrue(usuario.strip())

    def test_reglas_de_honestidad_en_el_prompt(self):
        sistema, _ = prompts.cargar("redactar_reporte")
        for regla in ("inversión", "alcance", "No inventes" if "No inventes" in sistema else "NO tenés"):
            self.assertIn(regla, sistema)

    def test_version_del_prompt_cambia_con_el_contenido(self):
        v = prompts.version("redactar_reporte")
        self.assertTrue(v.startswith("redactar_reporte@"))

    def test_render_incluye_los_anuncios(self):
        _, usuario = prompts.armar(
            "redactar_reporte", cliente="X", industria=None, notas_cliente=None,
            periodo_inicio="a", periodo_fin="b", competidores=["C1"],
            conteo={"nuevo": 1, "cambiado": 0, "pausado": 0, "continua": 0},
            anuncios=[{"referencia": "[A1]", "clasificacion": "nuevo", "competidor": "C1",
                       "plataforma": "meta", "titulo": "T", "texto": "X", "cta": None,
                       "tipo_creativo": "imagen", "fecha_inicio": None,
                       "visto_primero_en": "2026-09-19", "analisis": None}],
            periodo_anterior="nada")
        self.assertIn("[A1]", usuario)
        self.assertIn("NUEVO", usuario)


class TestRegistroDeFallos(unittest.TestCase):
    """Cada intento fallido queda registrado, no solo el fracaso total.

    Antes solo se anotaba si fallaban TODOS los proveedores. Como el último
    candidato es 'stub' y nunca falla, un reporte podía salir sin IA —seco,
    sin interpretación— sin dejar rastro de por qué. Pasó con un reporte real
    y no había forma de saber si fue un 429, una clave vencida o un modelo
    mal escrito.
    """

    def setUp(self):
        import sqlite3
        import tempfile
        from pathlib import Path

        from pulserival import db

        self.ruta = Path(tempfile.mkdtemp()) / "p.db"
        db.inicializar(self.ruta)
        self.con: sqlite3.Connection = db.conectar(self.ruta)
        self.addCleanup(self.con.close)

    def filas(self):
        return [dict(f) for f in self.con.execute(
            "SELECT proveedor, modelo, exito, detalle FROM uso_ia ORDER BY id")]

    def test_un_proveedor_sin_clave_queda_registrado(self):
        from pulserival.ia import ejecutar

        r = ejecutar(Peticion(tarea="analizar_anuncio", sistema="s", usuario="u",
                              datos={"titulo": "x"}), con=self.con)
        self.assertEqual(r.proveedor, "stub")
        fallos = [f for f in self.filas() if not f["exito"]]
        self.assertTrue(fallos, "los proveedores sin clave tienen que quedar anotados")
        self.assertTrue(all("sin clave" in f["detalle"] for f in fallos))

    def test_un_error_del_proveedor_queda_registrado_con_su_motivo(self):
        from unittest import mock

        from pulserival.ia import ejecutar
        from pulserival.ia.base import ProveedorError

        with mock.patch("pulserival.ia.groq_proveedor.ProveedorGroq.disponible", return_value=True), \
             mock.patch("pulserival.ia.groq_proveedor.ProveedorGroq.generar",
                        side_effect=ProveedorError("429 límite por minuto")):
            ejecutar(Peticion(tarea="analizar_anuncio", sistema="s", usuario="u",
                              datos={"titulo": "x"}), con=self.con, reintentos=1)
        detalles = " ".join(f["detalle"] or "" for f in self.filas() if not f["exito"])
        self.assertIn("429", detalles, "el motivo real tiene que quedar en la base")

    def test_el_comando_costos_muestra_los_fallos(self):
        from pulserival.ia import ejecutar

        ejecutar(Peticion(tarea="analizar_anuncio", sistema="s", usuario="u",
                          datos={"titulo": "x"}), con=self.con)
        self.con.commit()
        fallos = self.con.execute("SELECT SUM(1-exito) FROM uso_ia").fetchone()[0]
        self.assertGreater(fallos, 0)


class TestPausaEntreLlamadas(unittest.TestCase):
    def test_el_proveedor_local_no_espera(self):
        """La pausa existe por los límites de tasa de Groq y Gemini. Aplicarla
        también al proveedor local volvía la suite de tests 40 veces más lenta
        sin ganar nada."""
        import time

        from pulserival.ia import ejecutar

        inicio = time.monotonic()
        for _ in range(5):
            ejecutar(Peticion(tarea="analizar_anuncio", sistema="s", usuario="u",
                              datos={"titulo": "x"}))
        self.assertLess(time.monotonic() - inicio, 1.0)

    def test_la_pausa_esta_configurada_para_los_proveedores_reales(self):
        from pulserival import config

        self.assertGreater(config.pausa_entre_llamadas(), 0,
                           "sin pausa, 99 llamadas seguidas agotan el tier gratuito")
        self.assertGreater(config.max_anuncios_analizados(), 0)
