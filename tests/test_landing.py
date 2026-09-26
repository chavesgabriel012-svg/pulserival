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
