"""La landing: se genera de config/, no tiene precios escritos en el HTML."""
from __future__ import annotations

import re
import unicodedata
import unittest
from unittest import mock

from pulserival.landing import generador as construir_mod


class TestLanding(unittest.TestCase):
    def html(self, **parches):
        """Genera la página con una config controlada."""
        base = {
            "marca": "PulseRival", "dominio": "", "contacto": {},
            "titulo": "Título", "subtitulo": "Subtítulo",
            "argumentos": [{"titulo": "A", "texto": "B"}],
            "formulario": {"titulo": "Empecemos", "nota": "Nota"},
        }
        base.update(parches.pop("landing", {}))
        planes = parches.pop("planes", [
            {"clave": "prueba", "nombre": "Prueba gratis", "precio_usd": 0,
             "resumen": "r", "incluye": ["x"], "enlace_pago": ""},
            {"clave": "semanal", "nombre": "Semanal", "precio_usd": 100,
             "resumen": "r", "incluye": ["x"], "enlace_pago": ""},
        ])
        with mock.patch.object(construir_mod.config, "config_landing", return_value=base), \
             mock.patch.object(construir_mod.config, "config_planes",
                               return_value={"planes": planes}):
            entorno = construir_mod.Environment(autoescape=True)
            plantilla = entorno.from_string(
                (construir_mod.PLANTILLAS / "index.html.j2").read_text(encoding="utf-8"))
            return plantilla.render(**construir_mod.contexto())

    def test_los_precios_salen_del_yaml(self):
        h = self.html(planes=[{"clave": "mensual", "nombre": "Mensual", "precio_usd": 37,
                               "resumen": "r", "incluye": ["x"], "enlace_pago": ""}])
        self.assertIn("$37", h)

    def test_sin_enlace_de_pago_no_muestra_un_boton_muerto(self):
        # Un botón "Contratar" que no lleva a ningún lado es peor que no
        # tenerlo: el cliente hace clic y no pasa nada.
        h = self.html()
        self.assertNotIn("Contratar", h)
        self.assertIn("Hablemos", h)

    def test_con_enlace_de_pago_el_boton_cobra(self):
        h = self.html(planes=[{"clave": "semanal", "nombre": "Semanal", "precio_usd": 100,
                               "resumen": "r", "incluye": ["x"],
                               "enlace_pago": "https://pagos.ejemplo/abc"}])
        self.assertIn("https://pagos.ejemplo/abc", h)
        self.assertIn("Contratar", h)

    def test_el_plan_a_la_medida_no_inventa_un_precio(self):
        h = self.html(planes=[{"clave": "custom", "nombre": "A la medida",
                               "precio_usd": None, "resumen": "r", "incluye": ["x"],
                               "enlace_pago": ""}])
        self.assertIn("A convenir", h)
        self.assertNotIn("$None", h)

    def test_pide_el_contexto_del_negocio(self):
        # Es el campo que más sube la calidad del reporte (docs/05). Si se
        # cae del formulario, los reportes salen genéricos y nadie se entera.
        h = self.html()
        self.assertIn('name="contexto"', h)

    def test_pide_facebook_y_dominio_de_cada_competidor(self):
        h = self.html()
        for campo in ("comp1", "fb1", "web1"):
            self.assertIn(f'name="{campo}"', h)

    def test_sin_emojis_y_en_blanco_y_negro(self):
        h = self.html()
        self.assertEqual(
            [c for c in h if unicodedata.category(c) == "So" or ord(c) > 0x1F000], [])
        for color in set(re.findall(r"#[0-9a-fA-F]{6}", h)):
            r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
            self.assertLessEqual(max(r, g, b) - min(r, g, b), 8, f"{color} no es gris")

    def test_el_whatsapp_configurado_llega_al_javascript(self):
        h = self.html(landing={"contacto": {"whatsapp": "50688887777"}})
        self.assertIn('"50688887777"', h)

    def test_avisa_de_lo_que_falta_configurar(self):
        with mock.patch.object(construir_mod.config, "config_landing",
                               return_value={"contacto": {}, "dominio": ""}), \
             mock.patch.object(construir_mod.config, "config_planes",
                               return_value={"planes": [
                                   {"clave": "semanal", "precio_usd": 100,
                                    "enlace_pago": ""}]}):
            faltan = " ".join(construir_mod.pendientes())
        self.assertIn("contacto", faltan)
        self.assertIn("enlace_pago", faltan)

    def test_sin_pendientes_no_avisa_nada(self):
        with mock.patch.object(construir_mod.config, "config_landing",
                               return_value={"contacto": {"whatsapp": "506"},
                                             "dominio": "pulserival.com"}), \
             mock.patch.object(construir_mod.config, "config_planes",
                               return_value={"planes": [
                                   {"clave": "semanal", "precio_usd": 100,
                                    "enlace_pago": "https://pagos.ejemplo/x"}]}):
            self.assertEqual(construir_mod.pendientes(), [])


class TestContenidoNuevo(TestLanding):
    """Las secciones que explican el producto.

    Están en config/landing.yaml y la plantilla las dibuja solo si existen.
    Eso las hace fáciles de perder en un cambio de configuración, y son
    justamente lo que responde "¿qué me están vendiendo?".
    """

    SECCIONES = {
        "plataformas": {
            "titulo": "Las dos bibliotecas",
            "nota": "Son públicas.",
            "fuentes": [
                {"nombre": "Meta", "donde": "Facebook e Instagram",
                 "biblioteca": "Biblioteca de Anuncios de Meta",
                 "lee": ["El texto completo del anuncio"]},
                {"nombre": "Google", "donde": "Búsqueda, YouTube y Display",
                 "biblioteca": "Centro de Transparencia",
                 "lee": ["Cuántos anuncios tiene activos"],
                 "advertencia": "Google no publica el texto de todos los anuncios."},
            ],
        },
        "pasos": {"titulo": "Cómo se arma", "lista": [
            {"nombre": "Recolectamos", "texto": "Leemos las dos bibliotecas."},
            {"nombre": "Comparamos", "texto": "Contra lo que vimos antes."},
        ]},
        "limites": {"titulo": "Lo que no hace", "lista": [
            "No dice cuánto invierte su competencia."]},
    }

    def test_nombra_las_dos_plataformas(self):
        # Es lo primero que alguien quiere saber, y el pedido explícito:
        # que quede claro que se cubre Meta Y Google.
        h = self.html(landing=self.SECCIONES)
        self.assertIn("Meta", h)
        self.assertIn("Google", h)
        self.assertIn("Facebook e Instagram", h)
        self.assertIn("Búsqueda, YouTube y Display", h)

    def test_dice_que_de_Google_no_sale_el_texto(self):
        # La asimetría es real: el mapeo de config/fuentes.yaml saca el texto
        # de Meta y de Google saca señales de actividad. Prometer lo mismo de
        # las dos sería vender algo que el pipeline no entrega.
        h = self.html(landing=self.SECCIONES)
        self.assertIn("Google no publica el texto de todos los anuncios", h)

    def test_muestra_los_pasos_numerados(self):
        h = self.html(landing=self.SECCIONES)
        self.assertIn("Recolectamos", h)
        self.assertIn("Comparamos", h)
        self.assertIn(">01<", h.replace(" ", "").replace("\n", ""))

    def test_dice_lo_que_el_reporte_NO_hace(self):
        # Esta sección es el argumento de venta, no una disculpa: si alguien
        # promete la inversión de la competencia, está estimando.
        h = self.html(landing=self.SECCIONES)
        self.assertIn("No dice cuánto invierte su competencia", h)

    def test_sin_las_secciones_la_pagina_igual_se_arma(self):
        # config/landing.yaml lo edita una persona. Borrar una clave sin
        # querer no puede romper la página entera.
        h = self.html()
        self.assertIn("Empecemos", h)
        self.assertIn("<form", h)

    def test_las_secciones_nuevas_tampoco_traen_simbolos(self):
        h = self.html(landing=self.SECCIONES)
        self.assertEqual(
            [c for c in h if unicodedata.category(c) == "So" or ord(c) > 0x1F000], [])
