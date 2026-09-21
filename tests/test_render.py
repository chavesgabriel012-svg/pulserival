"""Renderizado a email: que el HTML salga armado y sin etiquetas roídas."""
from __future__ import annotations

import unittest

from pulserival.reporte import render

REPORTE = {
    "asunto": "Reporte · 2026-09-20",
    "preheader": "resumen",
    "cliente": "Gimnasio Fuerza Tica",
    "periodo_inicio": "2026-09-13",
    "periodo_fin": "2026-09-20",
    "conteo": {"nuevo": 2, "cambiado": 1, "pausado": 1, "continua": 3},
    "anuncios": [{"referencia": "[A1]", "competidor": "Vital Gym CR", "plataforma": "meta",
                  "clasificacion": "nuevo", "titulo": "Matrícula gratis",
                  "url_anuncio": "https://www.facebook.com/ads/library/?id=1"}],
    "cuerpo_md": ("## Lo más importante\n\nBajó el **precio** [A1] y subió la _urgencia_.\n\n"
                  "### Vital Gym CR\n\n- punto uno\n- punto dos\n\n"
                  "Ver [la fuente](https://ejemplo.test/x).\n"),
}


class TestRender(unittest.TestCase):
    def test_markdown_basico(self):
        html = render.markdown_a_html(REPORTE["cuerpo_md"])
        self.assertIn("<h2", html)
        self.assertIn("<h3", html)
        self.assertIn("<strong>precio</strong>", html)
        self.assertIn("<em>urgencia</em>", html)
        self.assertIn("<li", html)
        self.assertIn('href="https://ejemplo.test/x"', html)

    def test_escapa_html_del_anuncio(self):
        html = render.markdown_a_html("Texto con <script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_email_completo_incluye_anexo_de_fuentes(self):
        html = render.email_html(REPORTE)
        self.assertIn("Anexo: anuncios detectados", html)
        self.assertIn("facebook.com/ads/library", html)
        self.assertIn("No verá cuánto invierte su competencia", html,
                      "el reporte debe decir explícitamente que la inversión no existe")
        self.assertIn("Gimnasio Fuerza Tica", html)

    def test_texto_plano_y_whatsapp(self):
        texto = render.email_texto(REPORTE)
        self.assertIn("LO MÁS IMPORTANTE", texto)
        self.assertNotIn("**", texto)
        wa = render.whatsapp(REPORTE)
        self.assertIn("Gimnasio Fuerza Tica", wa)
        self.assertLess(len(wa), 1200, "el mensaje de WhatsApp tiene que ser corto")


class TestEscapadoDelEmail(unittest.TestCase):
    """El email lleva texto que escribió otra persona (el anuncio del
    competidor, raspado de una web). Si no se escapa, ese texto puede meter
    HTML en el correo que le llega a tu cliente."""

    def test_el_texto_del_anuncio_no_puede_meter_html(self):
        reporte = dict(REPORTE)
        reporte["anuncios"] = [{
            "referencia": "[A1]", "competidor": 'Vital "Gym" & Co', "plataforma": "meta",
            "clasificacion": "nuevo", "titulo": "Promo <b>ya</b>",
            "url_anuncio": 'https://f.test/?id=1" onmouseover="alert(1)',
        }]
        html = render.email_html(reporte)
        self.assertIn("&lt;b&gt;ya&lt;/b&gt;", html, "el HTML del anuncio debe quedar escapado")
        self.assertNotIn('onmouseover="alert(1)"', html, "no se puede escapar del atributo href")
        self.assertIn("&amp;", html, "el nombre del competidor debe quedar escapado")

    def test_el_cuerpo_del_reporte_no_queda_doble_escapado(self):
        html = render.email_html(REPORTE)
        self.assertIn("<strong>precio</strong>", html)
        self.assertNotIn("&lt;strong&gt;", html)


class TestEstructuraDelReporte(unittest.TestCase):
    """La estructura que el reporte le muestra al cliente.

    Cada cosa que se verifica acá está porque su ausencia rompe la propuesta
    de valor: sin tabla no hay comparación entre competidores, sin anexo no
    hay forma de verificar, y sin la advertencia sobre la inversión el
    cliente puede terminar creyendo que le vamos a dar un dato que no existe.
    """

    def reporte(self, **extra):
        r = dict(REPORTE)
        r["senales"] = [
            {"competidor": "Monge", "plataforma": "meta", "mensajes": 4, "piezas": 11,
             "nuevos": 2, "dias_mensaje_mas_viejo": 87},
            {"competidor": "Monge", "plataforma": "google", "mensajes": 3, "piezas": 3,
             "nuevos": 0, "dias_mensaje_mas_viejo": None},
        ]
        r["competidores"] = ["Monge", "Siman"]
        r["marca"] = "PulseRival"
        r["miniaturas"] = True
        r["anuncios"] = [dict(REPORTE["anuncios"][0],
                              creativo_url="https://cdn.test/a.jpg", variantes=3,
                              fecha_inicio="2026-06-29", sin_texto=False)]
        r.update(extra)
        return r

    def test_incluye_la_tabla_comparativa(self):
        html = render.email_html(self.reporte())
        self.assertIn("Comparativo de actividad", html)
        self.assertIn("Monge", html)
        self.assertIn("87", html, "los días del mensaje más viejo deben verse")
        self.assertIn("11", html, "las piezas deben verse")

    def test_explica_que_significa_cada_columna(self):
        html = render.email_html(self.reporte())
        self.assertIn("Un mensaje es un aviso distinto", html)
        self.assertIn("Cómo se hizo este reporte", html)

    def test_advierte_sobre_la_inversion(self):
        html = render.email_html(self.reporte())
        self.assertIn("No verá cuánto invierte su competencia", html)
        self.assertIn("Unión Europea", html)
        self.assertIn("se lo está estimando", html)

    def test_muestra_miniaturas_y_se_pueden_apagar(self):
        con = render.email_html(self.reporte())
        self.assertIn('<img src="https://cdn.test/a.jpg"', con)
        sin = render.email_html(self.reporte(miniaturas=False))
        self.assertNotIn('<img src="https://cdn.test/a.jpg"', sin)

    def test_el_anexo_marca_las_variantes_y_enlaza_la_fuente(self):
        html = render.email_html(self.reporte())
        self.assertIn("3 variantes", html)
        self.assertIn("Ver el anuncio en la fuente", html)

    def test_un_anuncio_sin_texto_lo_dice_en_vez_de_dejar_el_espacio_vacio(self):
        r = self.reporte()
        r["anuncios"] = [dict(r["anuncios"][0], sin_texto=True, titulo=None)]
        html = render.email_html(r)
        self.assertIn("La fuente no publica el texto", html)

    def test_la_marca_es_configurable(self):
        html = render.email_html(self.reporte(marca="Agencia X"))
        self.assertIn("Agencia X", html)
        self.assertNotIn(">PulseRival", html)

    def test_el_texto_plano_lleva_la_comparativa_y_la_advertencia(self):
        texto = render.email_texto(self.reporte())
        self.assertIn("Monge [Meta]", texto)
        self.assertIn("QUÉ NO INCLUYE ESTE REPORTE", texto)
