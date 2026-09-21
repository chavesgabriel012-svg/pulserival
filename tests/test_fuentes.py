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

    def test_sin_token_el_modo_auto_no_inventa_datos(self):
        # Caer a los datos de ejemplo en silencio pondría un gimnasio inventado
        # en el reporte de un cliente real. Mejor fallar y avisar.
        from pulserival.fuentes import FuenteError

        with self.assertRaises(FuenteError):
            obtener_fuente("meta", "auto")


class TestEntradaValidaSegunElActor(unittest.TestCase):
    """La entrada que mandamos tiene que respetar el esquema del actor.

    Este test existe por un error real: config/fuentes.yaml mandaba
    `sorting: recent`, que no es un valor permitido. El actor lo rechazó con
    un 400 y la corrida se perdió. El error no se podía ver sin llamar a
    Apify de verdad; ahora sí.

    Los valores permitidos son una copia del esquema del actor, tomada de
    api.apify.com el 2026-09-21. Si el actor cambia, la corrida va a fallar
    igual, pero al menos un typo nuestro se detecta acá y gratis.
    """

    # apify/facebook-ads-scraper
    ENUMS_META = {
        "activeStatus": ["", "active", "inactive"],
        "sorting": ["", "total_impressions", "relevancy_monthly_grouped"],
    }
    CAMPOS_META = {
        "startUrls", "resultsLimit", "onlyTotal", "includeAboutPage", "isDetailsPerAd",
        "activeStatus", "sorting", "onlyAdsNewerThan", "onlyAdsOlderThan",
        "enrichWithEcommerceData",
    }
    # pulsedata/google-ads-transparency-scraper
    ENUMS_GOOGLE = {
        "format": ["ALL", "TEXT", "IMAGE", "VIDEO"],
        "platform": ["ALL", "SEARCH", "YOUTUBE", "MAPS", "PLAY", "SHOPPING"],
        "advertiserMatch": ["best", "all"],
    }
    CAMPOS_GOOGLE = {
        "queries", "region", "format", "platform", "startDate", "endDate",
        "maxAdsPerQuery", "advertiserMatch", "includeDetails", "enrichPreviews",
        "proxyConfiguration",
    }

    def entrada(self, plataforma: str, competidor: dict) -> dict:
        return FuenteApify(plataforma, token="token-de-prueba").construir_entrada(competidor, 10)

    def test_meta_solo_usa_campos_y_valores_que_el_actor_acepta(self):
        entrada = self.entrada("meta", {"nombre": "Gollo", "meta_consulta": "Gollo"})
        for campo in entrada:
            self.assertIn(campo, self.CAMPOS_META, f"el actor de Meta no acepta '{campo}'")
        for campo, permitidos in self.ENUMS_META.items():
            if campo in entrada:
                self.assertIn(entrada[campo], permitidos,
                              f"{campo}={entrada[campo]!r} no está entre {permitidos}")

    def test_google_solo_usa_campos_y_valores_que_el_actor_acepta(self):
        entrada = self.entrada("google", {"nombre": "Gollo", "google_dominio": "gollo.com"})
        for campo in entrada:
            self.assertIn(campo, self.CAMPOS_GOOGLE, f"el actor de Google no acepta '{campo}'")
        for campo, permitidos in self.ENUMS_GOOGLE.items():
            if campo in entrada:
                self.assertIn(entrada[campo], permitidos,
                              f"{campo}={entrada[campo]!r} no está entre {permitidos}")

    def test_los_campos_obligatorios_van_siempre(self):
        self.assertIn("startUrls", self.entrada("meta", {"nombre": "X", "meta_consulta": "X"}))
        self.assertIn("queries", self.entrada("google", {"nombre": "X", "google_dominio": "x.com"}))
