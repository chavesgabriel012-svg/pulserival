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
        self.assertIn("no incluye datos de", html, "el pie debe aclarar qué no se puede saber")
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
