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

    def test_email_completo_incluye_el_detalle_y_las_fuentes(self):
        html = render.email_html(dict(REPORTE, por_competidor=[
            {"competidor": "Vital Gym CR", "total": 1, "piezas": 1,
             "meta": REPORTE["anuncios"], "google": []}]))
        self.assertIn("Detalle de anuncios", html)
        self.assertIn("facebook.com/ads/library", html)
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
        anuncio = {
            "referencia": "[A1]", "competidor": 'Vital "Gym" & Co', "plataforma": "meta",
            "clasificacion": "nuevo", "titulo": "Promo <b>ya</b>",
            "url_anuncio": 'https://f.test/?id=1" onmouseover="alert(1)',
        }
        reporte["anuncios"] = [anuncio]
        reporte["por_competidor"] = [{"competidor": 'Vital "Gym" & Co', "total": 1,
                                      "piezas": 1, "meta": [anuncio], "google": []}]
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
        a1 = dict(REPORTE["anuncios"][0], creativo_url="https://cdn.test/a.jpg",
                  variantes=3, fecha_inicio="2026-06-29", sin_texto=False,
                  competidor="Monge")
        a2 = dict(a1, referencia="[A2]", variantes=1, competidor="Monge",
                  plataforma="google", sin_texto=True, titulo=None)
        a3 = dict(a1, referencia="[A3]", variantes=1, competidor="Siman")
        r["anuncios"] = [a1, a2, a3]
        r["por_competidor"] = [
            {"competidor": "Monge", "total": 2, "piezas": 4, "meta": [a1], "google": [a2]},
            {"competidor": "Siman", "total": 1, "piezas": 1, "meta": [a3], "google": []},
        ]
        r.update(extra)
        return r

    def test_no_hay_color_en_el_reporte(self):
        """El reporte es en blanco y negro. Cualquier color que se cuele
        rompe la identidad y se nota en la impresión."""
        import re

        html = render.email_html(self.reporte())
        colores = set(re.findall(r"#[0-9a-fA-F]{6}", html))
        for color in colores:
            r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
            self.assertEqual({r, g, b}, {r},
                             f"{color} no es un gris: el reporte va en blanco y negro")

    def test_no_hay_emojis_en_el_reporte(self):
        from pulserival import util

        r = self.reporte()
        r["anuncios"] = [dict(r["anuncios"][0],
                              titulo=util.sin_emojis("Promo 👀 de la semana ✨"))]
        html = render.email_html(r)
        self.assertEqual(util.EMOJIS.findall(html), [])

    def test_incluye_la_tabla_comparativa(self):
        html = render.email_html(self.reporte())
        self.assertIn("Comparativo de actividad", html)
        self.assertIn("Monge", html)
        self.assertIn("87", html, "los días del mensaje más viejo deben verse")
        self.assertIn("11", html, "las piezas deben verse")

    def test_explica_que_significa_cada_columna_sin_sobrecargar(self):
        html = render.email_html(self.reporte())
        self.assertIn("Mensajes: avisos distintos al aire", html)
        self.assertIn("Piezas: veces que repite cada aviso", html)

    def test_la_aclaracion_sobre_inversion_va_al_pie_y_es_breve(self):
        """No se promociona lo que no se ofrece: es una línea de la nota de
        fuentes al final, no un bloque destacado."""
        html = render.email_html(self.reporte())
        self.assertIn("no publican inversión, alcance ni clics", html)
        cuerpo, pie = html.split("Detalle de anuncios", 1)
        self.assertNotIn("no publican inversión", cuerpo,
                         "la aclaración no puede ir antes del detalle")

    def test_muestra_miniaturas_y_se_pueden_apagar(self):
        con = render.email_html(self.reporte())
        self.assertIn('<img src="https://cdn.test/a.jpg"', con)
        sin = render.email_html(self.reporte(miniaturas=False))
        self.assertNotIn('<img src="https://cdn.test/a.jpg"', sin)

    def test_el_detalle_va_agrupado_por_competidor(self):
        """Una lista plana de 30 anuncios no se puede leer. El cliente busca
        'Monge' y quiere ver ahí sus anuncios, no revolverlos con los demás."""
        html = render.email_html(self.reporte())
        self.assertIn("Monge — 2 anuncios", html)
        self.assertIn("Siman — 1 anuncio", html)
        self.assertIn("Meta (Facebook e Instagram)", html)
        self.assertIn("Google", html)
        self.assertLess(html.index("Monge — 2 anuncios"), html.index("Siman — 1 anuncio"),
                        "el de mayor prioridad va primero")
        self.assertIn("3 piezas", html)
        self.assertIn("Ver en la fuente", html)

    def test_un_anuncio_sin_texto_lo_dice_en_vez_de_dejar_el_espacio_vacio(self):
        r = self.reporte()
        r["por_competidor"][0]["meta"][0] = dict(r["por_competidor"][0]["meta"][0],
                                                 sin_texto=True, titulo=None, texto=None)
        html = render.email_html(r)
        self.assertIn("La plataforma no publica el texto", html)

    def test_la_marca_es_configurable(self):
        html = render.email_html(self.reporte(marca="Agencia X"))
        self.assertIn("Agencia X", html)
        self.assertNotIn(">PulseRival", html)

    def test_el_texto_plano_lleva_la_comparativa_y_el_detalle(self):
        texto = render.email_texto(self.reporte())
        self.assertIn("Monge [Meta]", texto)
        self.assertIn("Monge — 2 anuncios", texto)
        self.assertIn("no publican inversión", texto)
        self.assertNotIn("**", texto)
