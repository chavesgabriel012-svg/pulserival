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
