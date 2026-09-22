"""Mapeo de la salida de los scrapers al modelo interno."""
from __future__ import annotations

import unittest

from pulserival.fuentes import obtener_fuente
from pulserival.fuentes.apify import FuenteApify

# Muestra con la forma que devuelve el actor de la Biblioteca de Anuncios.
# Los nombres son camelCase porque así los devuelve el actor de verdad; ver
# tests/fixtures/meta_anuncio_real.json. Escribirlos en snake_case, como
# estaban al principio, hacía pasar los tests con un mapeo que en producción
# no encontraba ni un solo campo.
ITEM_META = {
    "adArchiveID": "123456789",
    "pageName": "Vital Gym CR",
    "pageID": "987",
    "snapshot": {
        "body": {"text": "Plan mensual ¢19.900 sin matrícula"},
        "title": "Matrícula gratis en setiembre",
        "linkUrl": "https://vitalgym.test/promo",
        "ctaText": "Registrarte",
        "displayFormat": "IMAGE",
        "images": [{"originalImageUrl": "https://cdn.test/a.jpg"}],
    },
    "startDateFormatted": "2026-09-01",
    "publisherPlatform": ["FACEBOOK", "INSTAGRAM"],
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
        self.assertEqual(a.link_destino, "https://vitalgym.test/promo")
        self.assertEqual(a.url_anuncio, "https://www.facebook.com/ads/library/?id=123456789",
                         "el link a la ficha se arma con el id de archivo")
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


class TestMapeoConAnuncioReal(unittest.TestCase):
    """Mapeo contra una respuesta REAL del actor, guardada el 2026-09-21.

    Este test existe porque el mapeo original estaba escrito de memoria y
    casi todo estaba mal: el actor usa camelCase (`ctaText`, `linkUrl`,
    `originalImageUrl`) y yo había puesto snake_case. Con datos inventados
    los tests pasaban igual; recién con la primera llamada real se vio.

    La respuesta congelada es un anuncio de una óptica de Costa Rica, activo
    al momento de la corrida.
    """

    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path

        ruta = Path(__file__).parent / "fixtures" / "meta_anuncio_real.json"
        cls.item = json.loads(ruta.read_text(encoding="utf-8"))[0]
        cls.anuncio = FuenteApify("meta", token="prueba").mapear(cls.item)

    def test_trae_el_texto_del_anuncio(self):
        self.assertIn("examen visual", self.anuncio.texto)
        self.assertIn("Gollo Ópticas", self.anuncio.texto)

    def test_identifica_al_anunciante(self):
        self.assertEqual(self.anuncio.anunciante, "Gollo Ópticas")
        self.assertEqual(self.anuncio.metadata["anunciante_id"], "107953311029638")

    def test_las_fechas_son_fechas_y_no_marcas_de_tiempo(self):
        # startDate viene como 1782716400. Sin normalizar, el reporte le
        # mostraría ese número al cliente.
        self.assertEqual(self.anuncio.fecha_inicio, "2026-06-29")
        self.assertEqual(self.anuncio.fecha_fin, "2026-09-21")

    def test_reconoce_el_formato_declarado_por_la_fuente(self):
        self.assertEqual(self.anuncio.tipo_creativo, "imagen")

    def test_arma_el_link_a_la_ficha_publica(self):
        # El actor no devuelve este link, pero el reporte lo necesita para
        # que el cliente pueda abrir el anuncio y verificar lo que decimos.
        self.assertEqual(
            self.anuncio.url_anuncio,
            "https://www.facebook.com/ads/library/?id=27306236999005351",
        )

    def test_trae_el_boton_y_el_creativo(self):
        self.assertEqual(self.anuncio.cta, "Send message")
        self.assertTrue((self.anuncio.creativo_url or "").startswith("https://"))
        self.assertIn("fbcdn.net", self.anuncio.creativo_url)

    def test_guarda_el_contexto_util_de_la_plataforma(self):
        self.assertIn("FACEBOOK", self.anuncio.metadata["plataformas_publicacion"])
        self.assertEqual(self.anuncio.metadata["categoria_anunciante"], ["Eyewear"])

    def test_el_anuncio_no_queda_vacio(self):
        self.assertFalse(self.anuncio.vacio())
        self.assertTrue(self.anuncio.huella())


class TestRespuestasRealesCompletas(unittest.TestCase):
    """Las 15+15 respuestas reales de la prueba B, congeladas.

    Sirven para que cualquier cambio futuro en el mapeo se pueda verificar
    contra datos de verdad, sin volver a pagar una corrida.
    """

    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path

        base = Path(__file__).parent / "fixtures"
        cls.meta = json.loads((base / "meta_anuncios_reales.json").read_text(encoding="utf-8"))
        cls.google = json.loads((base / "google_anuncios_reales.json").read_text(encoding="utf-8"))

    def test_meta_mapea_los_quince_sin_perder_campos(self):
        fuente = FuenteApify("meta", token="prueba")
        anuncios = [fuente.mapear(i) for i in self.meta]
        self.assertEqual(len(anuncios), 15)
        self.assertEqual(sum(1 for a in anuncios if a.vacio()), 0, "ninguno debe quedar vacío")
        self.assertEqual(sum(1 for a in anuncios if not a.fecha_inicio), 0)
        self.assertEqual(sum(1 for a in anuncios if not a.url_anuncio), 0)
        self.assertEqual(len({a.huella() for a in anuncios}), 15, "las huellas deben ser únicas")

    def test_google_mapea_los_quince_y_respeta_el_formato_declarado(self):
        fuente = FuenteApify("google", token="prueba")
        anuncios = [fuente.mapear(i) for i in self.google]
        self.assertEqual(len(anuncios), 15)
        formatos = {}
        for a in anuncios:
            formatos[a.tipo_creativo] = formatos.get(a.tipo_creativo, 0) + 1
        # El actor declara 3 TEXT, 7 IMAGE y 5 VIDEO.
        self.assertEqual(formatos, {"texto": 3, "imagen": 7, "video": 5})
        self.assertTrue(all(a.url_anuncio and "adstransparency.google.com" in a.url_anuncio
                            for a in anuncios))

    def test_google_no_trae_texto_y_eso_esta_asumido(self):
        """Dato de producto, no un bug: el centro de transparencia de Google
        no publica el texto. Si algún día lo hace, este test falla y hay que
        actualizar el reporte para aprovecharlo."""
        fuente = FuenteApify("google", token="prueba")
        anuncios = [fuente.mapear(i) for i in self.google]
        con_texto = [a for a in anuncios if a.texto or a.titulo]
        self.assertEqual(con_texto, [], "el actor empezó a traer texto: aprovechalo en el reporte")
        # Lo que sí trae, y es con lo único que el reporte puede hablar:
        self.assertTrue(all(a.metadata.get("dias_al_aire") for a in anuncios))
        con_creativo = [a for a in anuncios if a.creativo_url]
        self.assertGreaterEqual(len(con_creativo), 13,
                                "casi todos deben traer algo que mirar en el anexo")
        videos = [a for a in anuncios if a.tipo_creativo == "video"]
        self.assertTrue(all(a.creativo_url for a in videos),
                        "los de video no traen imagen: se arma la miniatura de YouTube")

    def test_meta_trae_catalogos_dinamicos_con_plantillas(self):
        """Real: 6 de 15 anuncios eran de catálogo y traían '{{product.brand}}'
        en vez de texto. El reporte los marca como sin texto."""
        fuente = FuenteApify("meta", token="prueba")
        plantillas = [a for a in (fuente.mapear(i) for i in self.meta)
                      if "{{" in (a.texto or "")]
        self.assertTrue(plantillas, "el fixture debe incluir catálogos dinámicos")


class TestFiltroDeAnunciante(unittest.TestCase):
    """La búsqueda por dominio en Google devuelve a cualquiera que lo anuncie.

    Buscando siman.com aparecieron, junto a los de Almacenes Siman, los de
    "Publicentro de Guatemala Sociedad Anonima". Sin filtrar, esos anuncios
    se le contaban a SIMAN y el reporte afirmaba actividad que no era suya.
    """

    def anuncio(self, anunciante, anunciante_id, cid):
        from pulserival.fuentes.base import AnuncioCrudo
        return AnuncioCrudo(
            plataforma="google", fuente="apify:x", id_externo=cid,
            anunciante=anunciante, titulo=None, texto=None,
            metadata={"anunciante_id": anunciante_id},
        )

    def test_descarta_a_la_empresa_ajena_que_pauta_el_mismo_dominio(self):
        # Caso real: buscando siman.com aparecieron los de Almacenes Siman y
        # los de "Publicentro de Guatemala Sociedad Anonima".
        from pulserival.fuentes.apify import _solo_del_anunciante
        anuncios = [self.anuncio("Almacenes Siman", "AR074", f"c{i}") for i in range(5)]
        anuncios += [self.anuncio("Publicentro de Guatemala Sociedad Anonima", "AR146", "c9")]
        guardados, descartados = _solo_del_anunciante(anuncios, {"nombre": "Almacenes SIMAN"}, "google")
        self.assertEqual(len(guardados), 5)
        self.assertEqual(descartados, {"Publicentro de Guatemala Sociedad Anonima": 1})

    def test_conserva_a_la_agencia_y_tambien_a_la_empresa_misma(self):
        # Caso real: tiendamonge.com lo pauta "HAVAS COSTA RICA, S.A." (la
        # agencia, que es la dominante) y también "Financiera Monge S.A".
        # Quedarse solo con la dominante descartaba los 5 de la financiera, y
        # en la corrida siguiente aparecían como apagados.
        from pulserival.fuentes.apify import _solo_del_anunciante
        anuncios = [self.anuncio("HAVAS COSTA RICA, S.A.", "ARh", f"h{i}") for i in range(32)]
        anuncios += [self.anuncio("Financiera Monge S.A", "ARf", f"f{i}") for i in range(5)]
        guardados, descartados = _solo_del_anunciante(anuncios, {"nombre": "Tienda Monge"}, "google")
        self.assertEqual(len(guardados), 37)
        self.assertEqual(descartados, {})

    def test_las_palabras_genericas_no_emparejan(self):
        # "Sociedad Anonima" o "Costa Rica" no identifican a nadie: si
        # emparejaran, no se descartaría nunca nada.
        from pulserival.fuentes.apify import _solo_del_anunciante
        anuncios = [self.anuncio("Almacenes Siman", "AR074", f"c{i}") for i in range(5)]
        anuncios += [self.anuncio("Otra Cosa Costa Rica Sociedad Anonima", "AR146", "c9")]
        guardados, descartados = _solo_del_anunciante(
            anuncios, {"nombre": "Almacenes SIMAN Costa Rica Sociedad Anonima"}, "google")
        self.assertEqual(len(guardados), 5)
        self.assertIn("Otra Cosa Costa Rica Sociedad Anonima", descartados)

    def test_el_id_configurado_manda_sobre_el_conteo(self):
        from pulserival.fuentes.apify import _solo_del_anunciante
        anuncios = [self.anuncio("Agencia", "AR999", f"c{i}") for i in range(5)]
        anuncios += [self.anuncio("Almacenes Siman", "AR074", "c9")]
        guardados, descartados = _solo_del_anunciante(
            anuncios, {"nombre": "Almacenes SIMAN", "google_anunciante_id": "AR074"}, "google")
        self.assertEqual([a.id_externo for a in guardados], ["c9"])
        self.assertEqual(descartados, {"Agencia": 5})

    def test_un_solo_anunciante_no_se_toca(self):
        from pulserival.fuentes.apify import _solo_del_anunciante
        anuncios = [self.anuncio("Almacenes Siman", "AR074", f"c{i}") for i in range(3)]
        guardados, descartados = _solo_del_anunciante(anuncios, {"nombre": "SIMAN"}, "google")
        self.assertEqual(len(guardados), 3)
        self.assertEqual(descartados, {})

    def test_si_el_id_configurado_no_aparece_se_cae_a_las_otras_reglas(self):
        # Preferible revisar de más que devolver vacío por una config vieja.
        from pulserival.fuentes.apify import _solo_del_anunciante
        anuncios = [self.anuncio("Almacenes Siman", "AR074", f"c{i}") for i in range(3)]
        anuncios += [self.anuncio("Publicentro de Guatemala", "AR146", "c9")]
        guardados, descartados = _solo_del_anunciante(
            anuncios, {"nombre": "Almacenes SIMAN", "google_anunciante_id": "AR-viejo"}, "google")
        self.assertEqual(len(guardados), 3)
        self.assertEqual(descartados, {"Publicentro de Guatemala": 1})


class TestPaginaPorId(unittest.TestCase):
    """El id de página es la forma confiable de pedir los anuncios de Meta.

    La URL con el nombre obliga al actor a resolver la página, y con
    facebook.com/ArtelecCR/ esa resolución devolvió cero mientras la página
    tenía ~51 anuncios activos.
    """

    def fuente(self):
        from pulserival.fuentes.apify import FuenteApify
        return FuenteApify("meta", token="apify_prueba")

    def test_el_id_tiene_prioridad_sobre_la_url_con_nombre(self):
        entrada = self.fuente().construir_entrada(
            {"nombre": "Artelec", "meta_pagina_id": "123456789",
             "meta_pagina_url": "https://www.facebook.com/ArtelecCR/"}, 10)
        url = entrada["startUrls"][0]["url"]
        self.assertIn("view_all_page_id=123456789", url)
        self.assertNotIn("ArtelecCR", url)

    def test_sin_id_se_usa_la_url_con_nombre(self):
        entrada = self.fuente().construir_entrada(
            {"nombre": "Artelec",
             "meta_pagina_url": "https://www.facebook.com/ArtelecCR/"}, 10)
        self.assertEqual(entrada["startUrls"][0]["url"],
                         "https://www.facebook.com/ArtelecCR/")


class TestDescartadosSeReportan(unittest.TestCase):
    """Descartar anuncios en silencio es cómo se cuelan los errores."""

    def test_el_atributo_existe_aunque_no_se_haya_llamado_a_traer(self):
        from pulserival.fuentes.apify import FuenteApify
        self.assertEqual(FuenteApify("google", token="t").descartados, {})
        self.assertEqual(FuenteApify("meta", token="t").descartados, {})


class TestUrlDePagina(unittest.TestCase):
    """La barra final solo va cuando la URL es un nombre de página."""

    def test_al_nombre_de_pagina_se_le_agrega_la_barra(self):
        from pulserival.fuentes.apify import _url_de_pagina
        self.assertEqual(_url_de_pagina("https://www.facebook.com/ArtelecCR"),
                         "https://www.facebook.com/ArtelecCR/")
        self.assertEqual(_url_de_pagina("https://www.facebook.com/ArtelecCR/"),
                         "https://www.facebook.com/ArtelecCR/")

    def test_una_url_con_parametros_no_se_toca(self):
        # "?id=1590787819092831/" ya no es ese anuncio.
        from pulserival.fuentes.apify import _url_de_pagina
        url = "https://www.facebook.com/ads/library/?id=1590787819092831"
        self.assertEqual(_url_de_pagina(url), url)
        conparams = "https://www.facebook.com/ads/library/?active_status=active&view_all_page_id=123"
        self.assertEqual(_url_de_pagina(conparams), conparams)


class TestIdDePaginaEstricto(unittest.TestCase):
    """Un meta_pagina_id mal copiado no puede atribuir anuncios ajenos.

    El id se configura a mano. Si el número está mal, el actor devuelve los
    anuncios de OTRA página y llegan anuncios, así que no se marca ni
    sospechosa ni sin datos: se guardarían como del competidor sin que nada
    avise. En Meta el id configurado es estricto.
    """

    def ad(self, nombre, aid, cid):
        from pulserival.fuentes.base import AnuncioCrudo
        return AnuncioCrudo(plataforma="meta", fuente="x", id_externo=cid,
                            anunciante=nombre, metadata={"anunciante_id": aid})

    def filtrar(self, anuncios, competidor):
        from pulserival.fuentes.apify import _solo_del_anunciante
        return _solo_del_anunciante(anuncios, competidor, "meta")

    def test_el_id_correcto_deja_pasar_todo(self):
        ads = [self.ad("Artelec", "1881630875265820", f"a{i}") for i in range(5)]
        guardados, descartados = self.filtrar(
            ads, {"nombre": "Artelec", "meta_pagina_id": "1881630875265820"})
        self.assertEqual(len(guardados), 5)
        self.assertEqual(descartados, {})

    def test_un_id_equivocado_no_guarda_nada(self):
        ads = [self.ad("Otra Tienda", "999999", f"b{i}") for i in range(5)]
        guardados, descartados = self.filtrar(
            ads, {"nombre": "Artelec", "meta_pagina_id": "1881630875265820"})
        self.assertEqual(guardados, [], "eran anuncios de otra página")
        self.assertEqual(descartados, {"Otra Tienda": 5})

    def test_sin_id_configurado_no_se_filtra(self):
        # Pedir por URL de página devuelve una sola página: no hay nada que
        # filtrar y no se puede exigir un id que nadie configuró.
        ads = [self.ad("Artelec", "1881630875265820", f"c{i}") for i in range(3)]
        guardados, descartados = self.filtrar(ads, {"nombre": "Artelec"})
        self.assertEqual(len(guardados), 3)
        self.assertEqual(descartados, {})

    def test_mezcla_con_id_configurado_deja_solo_la_pagina_correcta(self):
        ads = ([self.ad("Artelec", "1881630875265820", f"d{i}") for i in range(3)]
               + [self.ad("Beetech", "777", "d9")])
        guardados, descartados = self.filtrar(
            ads, {"nombre": "Artelec", "meta_pagina_id": "1881630875265820"})
        self.assertEqual(len(guardados), 3)
        self.assertEqual(descartados, {"Beetech": 1})

    def test_en_google_un_id_viejo_no_deja_al_competidor_en_cero(self):
        # En Google la empresa pauta por medio de agencias y el id puede
        # quedar viejo: ahí se cae a las otras reglas en vez de vaciar.
        from pulserival.fuentes.apify import _solo_del_anunciante
        from pulserival.fuentes.base import AnuncioCrudo
        ads = [AnuncioCrudo(plataforma="google", fuente="x", id_externo=f"e{i}",
                            anunciante="Almacenes Siman",
                            metadata={"anunciante_id": "AR074"}) for i in range(5)]
        guardados, _ = _solo_del_anunciante(
            ads, {"nombre": "Almacenes SIMAN", "google_anunciante_id": "AR-viejo"}, "google")
        self.assertEqual(len(guardados), 5)
