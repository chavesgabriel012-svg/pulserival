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

from pulserival import marca
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

    def resumen_visible(self, html: str) -> str:
        """El aside del plan que se ve. Los otros se rinden con `hidden`.

        La pagina lleva el resumen de todos los planes para poder cambiarlo
        cuando la persona cambia el selector, asi que buscar un texto en el
        HTML entero no dice nada: hay que mirar el que queda visible.
        """
        asides = re.findall(r'<aside class="resumen sube"(.*?)</aside>', html, re.S)
        visibles = [a for a in asides if "hidden" not in a.split(">", 1)[0]]
        self.assertLessEqual(len(visibles), 1, "hay mas de un resumen visible")
        return visibles[0] if visibles else ""


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

    def test_el_ejemplo_del_reporte_va_agrupado_por_competidor(self):
        # El ejemplo existe para mostrar lo que mas cuesta explicar con
        # palabras: que el detalle va por competidor y, dentro, por
        # plataforma. Si se aplana en una lista, deja de tener sentido.
        h = self.portada(landing={"ejemplo": {
            "titulo": "Asi llega", "nota": "n", "pie": "Datos de muestra.",
            "asunto": "Competencia", "periodo": "Del 21 al 27",
            "conteo": [{"etiqueta": "Nuevos", "valor": "3", "destacado": True}],
            "competidores": [
                {"nombre": "Competidor A", "resumen": "3 anuncios",
                 "bloques": [{"plataforma": "Meta", "anuncios": [
                     {"referencia": "A1", "estado": "nuevo", "detalle": "d",
                      "texto": "Financiamiento a doce meses"}]}]},
                {"nombre": "Competidor B", "resumen": "1 anuncio",
                 "bloques": [{"plataforma": "Google", "anuncios": [
                     {"referencia": "B1", "estado": "se apago", "detalle": "d",
                      "texto": "Promocion"}]}]},
            ]}})
        self.assertIn("Competidor A", h)
        self.assertIn("Competidor B", h)
        # Cada competidor trae su propio bloque, no una lista suelta.
        self.assertEqual(h.count('class="correo-competidor"'), 2)
        self.assertIn("Meta", h)
        self.assertIn("Google", h)

    def test_el_ejemplo_dice_que_los_datos_son_de_muestra(self):
        # Son nombres inventados en una pagina que promete no inventar nada.
        # Sin el aviso, el ejemplo contradice al producto.
        h = self.portada(landing={"ejemplo": {
            "titulo": "t", "nota": "n", "pie": "Ejemplo con datos de muestra.",
            "asunto": "a", "periodo": "p", "conteo": [], "competidores": []}})
        self.assertIn("Ejemplo con datos de muestra.", h)

    def test_sin_ejemplo_configurado_la_portada_no_se_rompe(self):
        h = self.portada()
        self.assertIn("<h1", h)
        self.assertNotIn('class="correo-competidor"', h)

    def test_ya_no_lleva_la_barra_de_limites(self):
        h = self.portada(landing={"limites": {"titulo": "Lo que no hace",
                                              "lista": ["No dice la inversión"]}})
        self.assertNotIn("Lo que no hace", h)

    def test_sin_emojis_y_solo_con_la_paleta_de_marca(self):
        # Cambió a propósito: antes la regla era "todo gris". La identidad de
        # marca trae el naranja Pulso, así que prohibir el color dejó de tener
        # sentido. Lo que sí lo tiene es prohibir un color que nadie decidió.
        h = self.portada()
        self.assertEqual(
            [c for c in h if unicodedata.category(c) == "So" or ord(c) > 0x1F000], [])
        self.assertEqual(marca.fuera_de_paleta(h), [])


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

    def test_el_pulso_del_encabezado_para_con_menos_movimiento(self):
        # Es el unico movimiento permanente de la pagina y esta siempre a la
        # vista: si no se apaga con `prefers-reduced-motion`, a quien pidio
        # menos movimiento le queda latiendo arriba de todo para siempre.
        h = self.portada()
        self.assertIn("cruza-el-pulso", h)
        self.assertIn("header .pista { display:none; }", h)

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
        h = self.aplicar(plan_elegido="semanal")
        self.assertIn('value="semanal" selected', h)
        self.assertIn("Cada 7 días", self.resumen_visible(h))

    def test_el_resumen_visible_es_el_del_plan_elegido(self):
        # El selector y el resumen de al lado tienen que decir lo mismo: si no,
        # la persona lee un plan y envia otro.
        h = self.aplicar(plan_elegido="prueba")
        self.assertIn("Sin costo", self.resumen_visible(h))
        self.assertNotIn("Cada 7 días", self.resumen_visible(h))

    def test_sin_plan_elegido_no_se_rompe(self):
        # Un enlace viejo con un plan que ya no existe no puede dejar a
        # alguien sin poder aplicar.
        h = self.aplicar(plan_elegido=None)
        self.assertIn("<form", h)
        # Ninguna opcion viene marcada. Se busca en las <option> y no en el
        # HTML entero porque el guion de la pagina dice `selectedIndex`.
        self.assertNotRegex(h, r"<option[^>]*\sselected")
        self.assertEqual("", self.resumen_visible(h))
        # Sin resumen la columna se cierra en vez de dejar un hueco al lado.
        self.assertIn('class="columnas sola"', h)

    def test_avisa_que_el_cobro_todavia_no_esta_conectado(self):
        h = self.aplicar(plan_elegido="semanal")
        self.assertIn("cobro todavía no está conectado", self.resumen_visible(h))

    def test_el_plan_sin_costo_no_pide_tarjeta(self):
        h = self.aplicar(plan_elegido="prueba")
        self.assertIn("No se le pide tarjeta", self.resumen_visible(h))

    def test_el_whatsapp_configurado_llega_al_javascript(self):
        h = self.aplicar(landing={"contacto": {"whatsapp": "50688887777"}})
        self.assertIn('"50688887777"', h)

    def test_sin_emojis_y_solo_con_la_paleta_de_marca(self):
        h = self.aplicar()
        self.assertEqual(
            [c for c in h if unicodedata.category(c) == "So" or ord(c) > 0x1F000], [])
        self.assertEqual(marca.fuera_de_paleta(h), [])


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
