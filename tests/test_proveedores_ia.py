"""Los proveedores reales, con la respuesta HTTP simulada.

No hay forma de probar Groq y Gemini de verdad en los tests (harían falta
claves y red), pero sí se puede verificar lo que más se rompe: que se arme
bien el pedido y que se lea bien la respuesta.
"""
from __future__ import annotations

import unittest
from unittest import mock

import requests

from pulserival.ia.base import Peticion, ProveedorError
from pulserival.ia.gemini_proveedor import ProveedorGemini
from pulserival.ia.groq_proveedor import ProveedorGroq

PETICION = Peticion(tarea="redactar_reporte", sistema="Sos analista.", usuario="Escribí.",
                    temperatura=0.4, max_tokens=500)


def respuesta(codigo: int, cuerpo: dict, texto: str = "") -> mock.Mock:
    r = mock.Mock()
    r.status_code = codigo
    r.json.return_value = cuerpo
    r.text = texto or str(cuerpo)
    r.content = b"x"
    return r


class TestGroq(unittest.TestCase):
    def setUp(self):
        self.prov = ProveedorGroq(clave="gsk_prueba")

    def test_lee_respuesta_y_tokens(self):
        cuerpo = {"choices": [{"message": {"content": "## Reporte"}}],
                  "usage": {"prompt_tokens": 1200, "completion_tokens": 300}}
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)) as post:
            r = self.prov.generar(PETICION, "llama-3.3-70b-versatile")
        self.assertEqual(r.texto, "## Reporte")
        self.assertEqual((r.tokens_entrada, r.tokens_salida), (1200, 300))
        enviado = post.call_args.kwargs["json"]
        self.assertEqual(enviado["model"], "llama-3.3-70b-versatile")
        self.assertEqual(enviado["messages"][0]["role"], "system")
        self.assertEqual(enviado["temperature"], 0.4)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer gsk_prueba")

    def test_pide_json_cuando_corresponde(self):
        cuerpo = {"choices": [{"message": {"content": "{}"}}], "usage": {}}
        pet = Peticion(tarea="analizar_anuncio", sistema="s", usuario="u", json_estricto=True)
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)) as post:
            self.prov.generar(pet, "llama-3.1-8b-instant")
        self.assertEqual(post.call_args.kwargs["json"]["response_format"], {"type": "json_object"})

    def test_limite_de_cuota_es_error_manejado(self):
        with mock.patch("requests.post", return_value=respuesta(429, {})):
            with self.assertRaises(ProveedorError) as ctx:
                self.prov.generar(PETICION, "llama-3.3-70b-versatile")
        self.assertIn("429", str(ctx.exception))

    def test_sin_clave_avisa_donde_sacarla(self):
        with self.assertRaises(ProveedorError) as ctx:
            ProveedorGroq(clave=None).generar(PETICION, "x")
        self.assertIn("console.groq.com", str(ctx.exception))

    def test_red_caida_no_explota_crudo(self):
        with mock.patch("requests.post", side_effect=requests.ConnectionError("sin red")):
            with self.assertRaises(ProveedorError):
                self.prov.generar(PETICION, "x")


class TestGemini(unittest.TestCase):
    def setUp(self):
        self.prov = ProveedorGemini(clave="AIza_prueba")

    def test_lee_respuesta_en_partes(self):
        cuerpo = {"candidates": [{"content": {"parts": [{"text": "## Lo más "}, {"text": "importante"}]}}],
                  "usageMetadata": {"promptTokenCount": 4000, "candidatesTokenCount": 1200}}
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)) as post:
            r = self.prov.generar(PETICION, "gemini-2.5-pro")
        self.assertEqual(r.texto, "## Lo más importante")
        self.assertEqual((r.tokens_entrada, r.tokens_salida), (4000, 1200))
        url = post.call_args.args[0]
        self.assertIn("gemini-2.5-pro:generateContent", url)
        enviado = post.call_args.kwargs["json"]
        self.assertEqual(enviado["systemInstruction"]["parts"][0]["text"], "Sos analista.")
        self.assertEqual(enviado["generationConfig"]["maxOutputTokens"], 500)
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "AIza_prueba")

    def test_respuesta_vacia_es_error(self):
        with mock.patch("requests.post", return_value=respuesta(200, {"candidates": []})):
            with self.assertRaises(ProveedorError):
                self.prov.generar(PETICION, "gemini-2.5-pro")

    def test_error_http_incluye_el_detalle(self):
        with mock.patch("requests.post", return_value=respuesta(400, {"error": {"message": "modelo inválido"}},
                                                               texto='{"error":{"message":"modelo inválido"}}')):
            with self.assertRaises(ProveedorError) as ctx:
                self.prov.generar(PETICION, "modelo-que-no-existe")
        self.assertIn("modelo inválido", str(ctx.exception))


class TestFallbackEntreProveedores(unittest.TestCase):
    def test_si_el_primero_falla_se_usa_el_siguiente(self):
        from pulserival.ia import ejecutar

        with mock.patch("pulserival.ia.groq_proveedor.ProveedorGroq.disponible", return_value=True), \
             mock.patch("pulserival.ia.groq_proveedor.ProveedorGroq.generar",
                        side_effect=ProveedorError("429")):
            r = ejecutar(Peticion(tarea="analizar_anuncio", sistema="s", usuario="u",
                                  datos={"titulo": "Promo"}), reintentos=1)
        self.assertEqual(r.proveedor, "stub", "debe caer al siguiente candidato, no fallar")


class TestRespuestaCortada(unittest.TestCase):
    """Una respuesta truncada no se acepta nunca.

    Real: gemini-3.5-flash agotó el presupuesto de salida razonando y el
    borrador del cliente terminó en "Promociona un 20% de". Mi código lo
    aceptó como bueno. En los modelos 3.x el razonamiento consume el mismo
    maxOutputTokens que el texto visible, así que esto vuelve a pasar en
    cuanto el prompt crece.
    """

    def test_gemini_rechaza_una_respuesta_cortada(self):
        cuerpo = {
            "candidates": [{"finishReason": "MAX_TOKENS",
                            "content": {"parts": [{"text": "Promociona un 20% de"}]}}],
            "usageMetadata": {"promptTokenCount": 11000, "candidatesTokenCount": 439,
                              "thoughtsTokenCount": 3500},
        }
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)):
            with self.assertRaises(ProveedorError) as ctx:
                ProveedorGemini(clave="k").generar(PETICION, "gemini-3.5-flash")
        self.assertIn("cortó la respuesta", str(ctx.exception))
        self.assertIn("MAX_TOKENS", str(ctx.exception))

    def test_gemini_acepta_una_respuesta_completa(self):
        cuerpo = {
            "candidates": [{"finishReason": "STOP",
                            "content": {"parts": [{"text": "## Resumen ejecutivo\nTexto."}]}}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50,
                              "thoughtsTokenCount": 20},
        }
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)):
            r = ProveedorGemini(clave="k").generar(PETICION, "gemini-3.5-flash")
        self.assertIn("Resumen ejecutivo", r.texto)
        self.assertEqual(r.tokens_salida, 70, "el razonamiento se factura como salida")

    def test_el_nivel_de_razonamiento_se_envia_cuando_se_configura(self):
        from pulserival.ia.base import Peticion as P

        cuerpo = {"candidates": [{"finishReason": "STOP",
                                  "content": {"parts": [{"text": "ok"}]}}],
                  "usageMetadata": {}}
        pet = P(tarea="redactar_reporte", sistema="s", usuario="u", nivel_razonamiento="low")
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)) as post:
            ProveedorGemini(clave="k").generar(pet, "gemini-3.8-flash")
        self.assertEqual(post.call_args.kwargs["json"]["generationConfig"]["thinkingLevel"], "low")

    def test_sin_configurar_no_se_envia_el_campo(self):
        cuerpo = {"candidates": [{"finishReason": "STOP",
                                  "content": {"parts": [{"text": "ok"}]}}],
                  "usageMetadata": {}}
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)) as post:
            ProveedorGemini(clave="k").generar(PETICION, "gemini-2.5-flash")
        self.assertNotIn("thinkingLevel", post.call_args.kwargs["json"]["generationConfig"])

    def test_groq_rechaza_una_respuesta_cortada(self):
        cuerpo = {"choices": [{"finish_reason": "length",
                               "message": {"content": "texto a medias"}}],
                  "usage": {"completion_tokens": 4000}}
        with mock.patch("requests.post", return_value=respuesta(200, cuerpo)):
            with self.assertRaises(ProveedorError) as ctx:
                ProveedorGroq(clave="k").generar(PETICION, "llama-3.3-70b-versatile")
        self.assertIn("cortó la respuesta", str(ctx.exception))
