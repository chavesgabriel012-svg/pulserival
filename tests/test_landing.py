"""Las páginas públicas: la portada y la de aplicación.

Se generan de config/, no tienen precios escritos en el HTML, y son dos:
la portada explica y la de aplicación toma los datos. El formulario salió de
la portada a propósito, así que el reparto entre las dos páginas es parte de
lo que hay que dejar clavado.
"""
from __future__ import annotations

import re
import unicodedata
import unittest
from unittest import mock

from pulserival.landing import generador as construir_mod

LANDING_BASE = {
    "marca": "PulseRival", "dominio": "", "contacto": {},
    "titulo": "Título", "subtitulo": "Subtítulo",
    "argumentos": [{"titulo": "A", "texto": "B"}],
    "formulario": {"titulo": "Empecemos", "nota": "Nota"},
    "aplicar": {
        "titulo": "Empecemos con su reporte", "nota": "Nos toma dos minutos.",
        "competidores": {
            "titulo": "Sus competidores",
            "opcional": "Este bloque es opcional.",
            "explicacion": "Si no nos los indica, los deducimos nosotros.",
            "ayuda": "Sirve su página de Facebook o su dominio.",
        },
        "cierre": "No se cobra nada hasta que usted lo confirme.",
    },
}
PLANES_BASE = [
    {"clave": "prueba", "nombre": "Prueba gratis", "precio_usd": 0, "resumen": "r",
     "incluye": ["x"], "enlace_pago": "", "cadencia_texto": "Una sola vez",
     "competidores_max": 3, "para_quien": "Para ver de qué se trata."},
    {"clave": "semanal", "nombre": "Semanal", "precio_usd": 100, "resumen": "r",
     "incluye": ["x"], "enlace_pago": "", "cadencia_texto": "Cada 7 días",
     "competidores_max": 6, "para_quien": "Para retail."},
]


class CasoPaginas(unittest.TestCase):
    """Rinde una página con una configuración controlada."""

    def render(self, plantilla, **parches):
        landing = dict(LANDING_BASE)
        landing.update(parches.pop("landing", {}))
        planes = parches.pop("planes", PLANES_BASE)
        with mock.patch.object(construir_mod.config, "config_landing", return_value=landing), \
             mock.patch.object(construir_mod.config, "config_planes",
                               return_value={"planes": planes}):
            datos = construir_mod.contexto()
        datos.update({"inicio_url": "index.html", "aplicar_url": "aplicar.html"})
        datos.update(parches)
        return construir_mod.render(plantilla, datos)

    def portada(self, **kw):
        return self.render("index.html.j2", **kw)

    def aplicar(self, **kw):
        return self.render("aplicar.html.j2", **kw)


class TestPortada(CasoPaginas):
    def test_los_precios_salen_del_yaml(self):
        h = self.portada(planes=[{"clave": "mensual", "nombre": "Mensual",
                                  "precio_usd": 37, "resumen": "r", "incluye": ["x"],
                                  "enlace_pago": ""}])
        self.assertIn("$37", h)

    def test_el_plan_a_la_medida_no_inventa_un_precio(self):
        h = self.portada(planes=[{"clave": "custom", "nombre": "A la medida",
                                  "precio_usd": None, "resumen": "r", "incluye": ["x"],
                                  "enlace_pago": ""}])
        self.assertIn("A convenir", h)
        self.assertNotIn("$None", h)

    def test_la_portada_ya_NO_lleva_el_formulario(self):
        # El pedido fue sacarlo del todo: la portada explica y la página de
        # aplicación toma los datos. Un formulario en medio de la explicación
        # corta la lectura de quien todavía está decidiendo.
        h = self.portada()
        self.assertNotIn("<form", h)
        self.assertNotIn('name="empresa"', h)

    def test_cada_plan_lleva_a_la_pagina_de_aplicacion_con_su_plan(self):
        h = self.portada()
        self.assertIn('href="aplicar.html?plan=prueba"', h)
        self.assertIn('href="aplicar.html?plan=semanal"', h)

    def test_explica_en_que_se_diferencian_los_planes(self):
        # Es la pregunta que frena la compra: cada cuánto llega y a cuántos
        # competidores cubre.
        h = self.portada()
        self.assertIn("Cada 7 días", h)
        self.assertIn("Hasta 6", h)
        self.assertIn("Frecuencia", h)

    def test_un_plan_sin_tope_de_competidores_lo_dice(self):
        h = self.portada(planes=[{"clave": "custom", "nombre": "A la medida",
                                  "precio_usd": None, "resumen": "r", "incluye": ["x"],
                                  "enlace_pago": "", "competidores_max": None}])
        self.assertIn("Sin tope", h)

    def test_nombra_las_dos_plataformas(self):
        h = self.portada(landing={"plataformas": {
            "titulo": "Las dos bibliotecas", "fuentes": [
                {"nombre": "Meta", "donde": "Facebook e Instagram",
                 "biblioteca": "Biblioteca de Anuncios", "lee": ["El texto"]},
                {"nombre": "Google", "donde": "Búsqueda, YouTube y Display",
                 "biblioteca": "Centro de Transparencia", "lee": ["Cuántos anuncios"],
                 "nota": "Google no publica el texto de todos los anuncios."},
            ]}})
        self.assertIn("Facebook e Instagram", h)
        self.assertIn("Búsqueda, YouTube y Display", h)
        # La asimetría es real y la página no la puede esconder.
        self.assertIn("Google no publica el texto de todos los anuncios", h)

    def test_muestra_los_pasos_numerados(self):
        h = self.portada(landing={"pasos": {"titulo": "Cómo", "lista": [
            {"nombre": "Recolectamos", "texto": "t"},
            {"nombre": "Comparamos", "texto": "t"}]}})
        self.assertIn("Recolectamos", h)
        self.assertIn(">01<", h.replace(" ", "").replace("\n", ""))

    def test_ya_no_lleva_la_barra_de_limites(self):
        h = self.portada(landing={"limites": {"titulo": "Lo que no hace",
                                              "lista": ["No dice la inversión"]}})
        self.assertNotIn("Lo que no hace", h)

    def test_sin_emojis_y_en_blanco_y_negro(self):
        h = self.portada()
        self.assertEqual(
            [c for c in h if unicodedata.category(c) == "So" or ord(c) > 0x1F000], [])
        for color in set(re.findall(r"#[0-9a-fA-F]{6}", h)):
            r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
            self.assertLessEqual(max(r, g, b) - min(r, g, b), 8, f"{color} no es gris")


class TestAnimaciones(CasoPaginas):
    def test_sin_javascript_el_contenido_igual_se_ve(self):
        # La clase `js` la pone un script del <head>. Si el CSS escondiera los
        # bloques sin esa condición, quien tenga JavaScript apagado vería una
        # página en blanco.
        h = self.portada()
        self.assertIn(".js .sube", h)
        self.assertNotIn("\n  .sube { opacity:0", h)

    def test_respeta_a_quien_pidio_menos_movimiento(self):
        h = self.portada()
        self.assertIn("prefers-reduced-motion", h)
        self.assertIn("matchMedia('(prefers-reduced-motion: reduce)')", h)

    def test_el_logo_vuelve_al_inicio(self):
        h = self.portada()
        self.assertIn('class="marca-enlace" href="index.html"', h)
        self.assertIn("ir al inicio", h)


class TestAplicar(CasoPaginas):
    def test_tiene_el_formulario_completo(self):
        h = self.aplicar()
        for campo in ("empresa", "contacto", "email", "plan", "industria", "contexto"):
            self.assertIn(f'name="{campo}"', h)

    def test_pide_el_contexto_del_negocio(self):
        # Es el campo que más sube la calidad del reporte (docs/05). Si se
        # cae del formulario, los reportes salen genéricos y nadie se entera.
        self.assertIn('name="contexto"', self.aplicar())

    def test_los_competidores_son_opcionales_y_lo_dice(self):
        h = self.aplicar()
        self.assertIn("Opcional", h)
        self.assertIn("los deducimos nosotros", h)

    def test_no_promete_acertar_los_competidores_deducidos(self):
        # Deducirlos y prometer que son los correctos son cosas distintas.
        # Lo segundo se descubre en el primer reporte, y mal.
        h = self.aplicar(landing={"aplicar": {
            **LANDING_BASE["aplicar"],
            "competidores": {**LANDING_BASE["aplicar"]["competidores"],
                             "explicacion": "Lo hacemos bien, pero no le prometemos acertar."},
        }})
        self.assertIn("no le prometemos acertar", h)

    def test_pide_facebook_y_dominio_de_cada_competidor(self):
        h = self.aplicar()
        for campo in ("comp1", "fb1", "web1"):
            self.assertIn(f'name="{campo}"', h)

    def test_el_plan_de_la_url_llega_preseleccionado(self):
        h = self.aplicar(plan_elegido="semanal", resumen_plan=PLANES_BASE[1])
        self.assertIn('value="semanal" selected', h)
        self.assertIn("Cada 7 días", h)

    def test_sin_plan_elegido_no_se_rompe(self):
        # Un enlace viejo con un plan que ya no existe no puede dejar a
        # alguien sin poder aplicar.
        h = self.aplicar(plan_elegido=None, resumen_plan=None)
        self.assertIn("<form", h)
        self.assertNotIn("selected", h)

    def test_avisa_que_el_cobro_todavia_no_esta_conectado(self):
        h = self.aplicar(plan_elegido="semanal", resumen_plan=PLANES_BASE[1])
        self.assertIn("cobro todavía no está conectado", h)

    def test_el_plan_sin_costo_no_pide_tarjeta(self):
        h = self.aplicar(plan_elegido="prueba", resumen_plan=PLANES_BASE[0])
        self.assertIn("No se le pide tarjeta", h)

    def test_el_whatsapp_configurado_llega_al_javascript(self):
        h = self.aplicar(landing={"contacto": {"whatsapp": "50688887777"}})
        self.assertIn('"50688887777"', h)

    def test_sin_emojis_y_en_blanco_y_negro(self):
        h = self.aplicar()
        self.assertEqual(
            [c for c in h if unicodedata.category(c) == "So" or ord(c) > 0x1F000], [])
        for color in set(re.findall(r"#[0-9a-fA-F]{6}", h)):
            r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
            self.assertLessEqual(max(r, g, b) - min(r, g, b), 8, f"{color} no es gris")


class TestGenerador(unittest.TestCase):
    def test_escribe_las_dos_paginas(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            paginas = construir_mod.construir(Path(tmp))
        self.assertEqual([p.name for p in paginas], ["index.html", "aplicar.html"])

    def test_en_estatico_los_enlaces_son_archivos(self):
        # La página tiene que funcionar abierta con doble clic desde una
        # carpeta, sin servidor: ahí `/aplicar` no existe.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            paginas = construir_mod.construir(Path(tmp))
            portada = paginas[0].read_text(encoding="utf-8")
        self.assertIn("aplicar.html", portada)
        self.assertNotIn('href="/aplicar"', portada)

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


if __name__ == "__main__":
    unittest.main()
