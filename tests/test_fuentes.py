"""Mapeo de la salida de los scrapers al modelo interno."""
from __future__ import annotations

import unittest

from pulserival.fuentes import obtener_fuente
from pulserival.fuentes.apify import FuenteApify

# Muestra con la forma que devuelve el actor de la Biblioteca de Anuncios.
ITEM_META = {
    "adArchiveID": "123456789",
    "pageName": "Vital Gym CR",
    "pageID": "987",
    "snapshot": {
        "body": {"text": "Plan mensual ¢19.900 sin matrícula"},
        "title": "Matrícula gratis en setiembre",
        "link_url": "https://vitalgym.test/promo",
        "cta_text": "Registrarte",
        "images": [{"original_image_url": "https://cdn.test/a.jpg"}],
    },
    "startDateFormatted": "2026-09-01",
    "publisherPlatform": ["FACEBOOK", "INSTAGRAM"],
    "url": "https://www.facebook.com/ads/library/?id=123456789",
}

ITEM_GOOGLE = {
    "creativeId": "CR-1",
    "advertiserName": "Vital Gym CR",
    "advertiserId": "AR01",
    "headline": "Gimnasio en Escazú",
    "description": "Plan mensual sin contrato",
    "destinationUrl": "https://vitalgym.test/",
    "previewImageUrl": "https://cdn.test/g.png",
    "firstShown": "2026-08-20",
    "lastShown": "2026-09-19",
    "format": "TEXT",
}


class TestMapeoApify(unittest.TestCase):
    def fuente(self, plataforma: str) -> FuenteApify:
        return FuenteApify(plataforma, token="token-de-prueba")

    def test_mapea_meta_desde_campos_anidados(self):
        a = self.fuente("meta").mapear(ITEM_META)
        self.assertEqual(a.id_externo, "123456789")
        self.assertEqual(a.anunciante, "Vital Gym CR")
        self.assertEqual(a.titulo, "Matrícula gratis en setiembre")
        self.assertIn("19.900", a.texto)
        self.assertEqual(a.cta, "Registrarte")
        self.assertEqual(a.creativo_url, "https://cdn.test/a.jpg")
        self.assertEqual(a.plataforma, "meta")
        self.assertFalse(a.vacio())

    def test_mapea_google(self):
        a = self.fuente("google").mapear(ITEM_GOOGLE)
        self.assertEqual(a.id_externo, "CR-1")
        self.assertEqual(a.titulo, "Gimnasio en Escazú")
        self.assertEqual(a.fecha_inicio, "2026-08-20")
        self.assertEqual(a.link_destino, "https://vitalgym.test/")

    def test_item_incompleto_no_explota(self):
        a = self.fuente("meta").mapear({"adArchiveID": "1"})
        self.assertTrue(a.vacio(), "un item sin contenido se descarta, no rompe la corrida")

    def test_entrada_meta_usa_pagina_si_existe(self):
        entrada = self.fuente("meta").construir_entrada(
            {"nombre": "Vital", "meta_pagina_url": "https://facebook.com/vital"}, 10)
        self.assertEqual(entrada["startUrls"][0]["url"], "https://facebook.com/vital/")
        self.assertEqual(entrada["resultsLimit"], 10)

    def test_entrada_meta_cae_a_busqueda_por_pais(self):
        entrada = self.fuente("meta").construir_entrada({"nombre": "Vital Gym CR"}, 10)
        url = entrada["startUrls"][0]["url"]
        self.assertIn("country=CR", url, "la búsqueda debe quedar acotada a Costa Rica")
        self.assertIn("Vital", url)

    def test_entrada_google_prefiere_el_dominio(self):
        entrada = self.fuente("google").construir_entrada(
            {"nombre": "Vital", "google_dominio": "vitalgym.test"}, 15)
        self.assertEqual(entrada["queries"], ["vitalgym.test"])
        self.assertEqual(entrada["region"], "CR")
        self.assertEqual(entrada["maxAdsPerQuery"], 15)


class TestFuenteDemo(unittest.TestCase):
    def test_demo_devuelve_anuncios_sin_red(self):
        anuncios = obtener_fuente("meta", "demo").traer({"id": 1, "nombre": "Vital Gym CR"})
        self.assertTrue(anuncios)
        self.assertTrue(all(a.plataforma == "meta" for a in anuncios))
        self.assertTrue(all(a.huella() for a in anuncios))

    def test_sin_token_apify_cae_a_demo(self):
        fuente = obtener_fuente("meta", "auto")
        self.assertTrue(fuente.nombre.startswith("demo:"),
                        "sin APIFY_TOKEN no debe intentar cobrar ni fallar")
