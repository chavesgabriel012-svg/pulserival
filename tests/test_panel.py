"""El panel de revisión.

Lo que más importa probar acá no son las pantallas, sino los seguros. El panel
puede mandarle un correo a un cliente real y eso no se deshace. Hay cuatro
cosas que tienen que ser ciertas siempre:

  1. sin contraseña configurada, el panel no se sirve (no queda abierto);
  2. sin sesión, no se llega a nada;
  3. un POST sin el token no pasa (si no, una página ajena podría mandar el
     reporte usando la cookie del navegador);
  4. no se envía un reporte que usted no revisó, ni se envía dos veces.
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from pulserival import config, db
from tests.base import CasoBase

CLAVE = "clave-de-prueba"


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    if not m:
        raise AssertionError("La página no trae token CSRF")
    return m.group(1)


class CasoPanel(CasoBase):
    clave = CLAVE

    def setUp(self):
        super().setUp()
        previo = os.environ.get("PULSERIVAL_PANEL_CLAVE")
        os.environ["PULSERIVAL_PANEL_CLAVE"] = self.clave or ""
        os.environ["PULSERIVAL_SECRET"] = "secreto-de-prueba"

        def restaurar():
            if previo is None:
                os.environ.pop("PULSERIVAL_PANEL_CLAVE", None)
            else:
                os.environ["PULSERIVAL_PANEL_CLAVE"] = previo
            os.environ.pop("PULSERIVAL_SECRET", None)

        self.addCleanup(restaurar)

        from pulserival.web import panel as modulo
        from pulserival.web.app import crear_app

        modulo._INTENTOS.clear()
        self.app = crear_app(str(self.ruta))
        self.app.config["TESTING"] = True
        self.web = self.app.test_client()
        self.cid = self.cliente(clave="gym", nombre_empresa="Gimnasio Fuerza Tica",
                                contacto_email="ana@fuerzatica.test")
        self.rid = db.insertar(self.con, "reportes_generados", {
            "cliente_id": self.cid, "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-01-07", "asunto": "Reporte de prueba",
            "borrador_md": "## Resumen\n\nMonge sacó tres anuncios nuevos [A1].",
            "validacion_json": db.json_o_nada({
                "aprobado": False, "palabras": 120, "cobertura_importantes": "1/3",
                "problemas": [{"tipo": "sin_referencias", "detalle": "No cita ni un anuncio."}],
                "avisos": [{"tipo": "muy_corto", "detalle": "Solo 120 palabras."}],
            }),
            "datos_json": db.json_o_nada({
                "conteo": {"nuevo": 3, "cambiado": 1, "pausado": 0, "continua": 2},
                # La forma real: `datos_json` guarda la lista plana de anuncios y
                # el agrupado por competidor se deriva. Un fixture con
                # `por_competidor` ya armado probaría un camino que no existe.
                "anuncios": [{
                    "anuncio_id": 1, "referencia": "A1", "competidor": "Tienda Monge",
                    "plataforma": "meta", "prioridad": 1, "clasificacion": "nuevo",
                    "titulo": "Cero intereses", "texto": "Doce meses sin intereses.",
                    "variantes": 2, "url_anuncio": "https://example.test/a1",
                }],
            }),
        })
        self.con.commit()

    def entrar(self) -> None:
        pag = self.web.get("/panel/entrar").get_data(as_text=True)
        r = self.web.post("/panel/entrar", data={"csrf": _csrf(pag), "clave": CLAVE})
        assert r.status_code == 302, r.status_code

    def token(self, ruta: str = "/panel/") -> str:
        return _csrf(self.web.get(ruta).get_data(as_text=True))


class TestSinContrasena(CasoPanel):
    """Sin PULSERIVAL_PANEL_CLAVE el panel NO queda abierto."""

    clave = ""

    def test_ninguna_ruta_del_panel_responde(self):
        for ruta in ("/panel/", "/panel/entrar", "/panel/gasto",
                     f"/panel/reporte/{self.rid}"):
            with self.subTest(ruta=ruta):
                r = self.web.get(ruta)
                self.assertEqual(r.status_code, 503)
                # Y dice qué falta, para no perder tiempo adivinando.
                self.assertIn("PULSERIVAL_PANEL_CLAVE", r.get_data(as_text=True))

    def test_tampoco_se_puede_enviar(self):
        r = self.web.post(f"/panel/reporte/{self.rid}/enviar",
                          data={"confirmacion": "Gimnasio Fuerza Tica"})
        self.assertEqual(r.status_code, 503)

    def test_la_landing_sigue_funcionando(self):
        # El panel apagado no puede tumbar la parte pública.
        self.assertEqual(self.web.get("/").status_code, 200)


class TestAcceso(CasoPanel):
    def test_sin_sesion_redirige_al_login(self):
        r = self.web.get("/panel/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/panel/entrar", r.headers["Location"])

    def test_el_login_es_alcanzable(self):
        # Antes fallaba: `before_request` solo eximía el GET del login, así que
        # el POST de la contraseña caía en la comprobación de sesión y
        # redirigía al login otra vez. El panel era inalcanzable.
        pag = self.web.get("/panel/entrar")
        self.assertEqual(pag.status_code, 200)
        r = self.web.post("/panel/entrar",
                          data={"csrf": _csrf(pag.get_data(as_text=True)), "clave": CLAVE})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/panel/")

    def test_contrasena_incorrecta(self):
        pag = self.web.get("/panel/entrar").get_data(as_text=True)
        r = self.web.post("/panel/entrar", data={"csrf": _csrf(pag), "clave": "otra"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.web.get("/panel/").status_code, 302)

    def test_limita_los_intentos(self):
        from pulserival.web import panel as modulo

        pag = self.web.get("/panel/entrar").get_data(as_text=True)
        tok = _csrf(pag)
        for _ in range(modulo.MAX_INTENTOS):
            self.web.post("/panel/entrar", data={"csrf": tok, "clave": "otra"})
        r = self.web.post("/panel/entrar", data={"csrf": tok, "clave": "otra"})
        self.assertEqual(r.status_code, 429)

    def test_el_login_no_redirige_a_un_dominio_ajeno(self):
        # Un `volver` externo convertiría el login en un redirector abierto,
        # que es con lo que se arman los enlaces de phishing.
        pag = self.web.get("/panel/entrar").get_data(as_text=True)
        r = self.web.post("/panel/entrar", data={
            "csrf": _csrf(pag), "clave": CLAVE, "volver": "https://sitio-ajeno.test/robar"})
        self.assertEqual(r.headers["Location"], "/panel/")

    def test_salir_cierra_la_sesion(self):
        self.entrar()
        self.web.post("/panel/salir", data={"csrf": self.token()})
        self.assertEqual(self.web.get("/panel/").status_code, 302)


class TestCSRF(CasoPanel):
    def test_ningun_post_pasa_sin_token(self):
        self.entrar()
        rutas = [
            (f"/panel/reporte/{self.rid}/guardar", {"cuerpo": "texto"}),
            (f"/panel/reporte/{self.rid}/enviar", {"confirmacion": "Gimnasio Fuerza Tica"}),
            (f"/panel/reporte/{self.rid}/descartar", {"motivo": "no sirve"}),
        ]
        for ruta, datos in rutas:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.web.post(ruta, data=datos).status_code, 400)

    def test_un_token_de_otra_sesion_no_sirve(self):
        self.entrar()
        r = self.web.post(f"/panel/reporte/{self.rid}/descartar",
                          data={"csrf": "token-inventado", "motivo": "no sirve"})
        self.assertEqual(r.status_code, 400)


class TestBandeja(CasoPanel):
    def test_muestra_el_reporte_pendiente_con_su_control_de_calidad(self):
        self.entrar()
        texto = self.web.get("/panel/").get_data(as_text=True)
        self.assertIn("Gimnasio Fuerza Tica", texto)
        self.assertIn("Revisar", texto)      # el control de calidad no lo aprobó
        self.assertIn("120 palabras", texto)
        self.assertIn("1/3", texto)          # cobertura

    def test_los_enviados_no_estan_en_los_pendientes(self):
        db.actualizar(self.con, "reportes_generados", self.rid,
                      {"estado": "enviado", "enviado_en": "2026-01-08 10:00"})
        self.con.commit()
        self.entrar()
        texto = self.web.get("/panel/").get_data(as_text=True)
        self.assertIn("No hay nada pendiente", texto)
        self.assertIn("Ya decididos", texto)


class TestRevisar(CasoPanel):
    def test_ver_muestra_los_problemas_y_el_insumo(self):
        self.entrar()
        texto = self.web.get(f"/panel/reporte/{self.rid}").get_data(as_text=True)
        self.assertIn("sin_referencias", texto)
        self.assertIn("Tienda Monge", texto)
        self.assertIn("https://example.test/a1", texto)   # el enlace para verificar

    def test_guardar_registra_el_diff_igual_que_el_CLI(self):
        # El panel no puede ser un atajo que se saltee el dataset: el diff entre
        # el borrador y la versión enviada es el activo del proyecto.
        self.entrar()
        r = self.web.post(f"/panel/reporte/{self.rid}/guardar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"),
            "cuerpo": "## Resumen\n\nVersión mía, bastante más larga y distinta.",
            "etiqueta": "tono", "razon": "sonaba a folleto"})
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM reportes_generados WHERE id = ?", (self.rid,))
        self.assertEqual(fila["estado"], "revisado")
        self.assertIn("Versión mía", fila["final_md"])
        edicion = db.fila(self.con,
                          "SELECT * FROM ediciones_registradas WHERE reporte_id = ?", (self.rid,))
        self.assertIsNotNone(edicion)
        self.assertEqual(edicion["etiqueta"], "tono")
        self.assertEqual(edicion["razon"], "sonaba a folleto")
        self.assertTrue(edicion["diff_unificado"])

    def test_aprobar_sin_cambios_tambien_queda_registrado(self):
        self.entrar()
        original = db.fila(self.con, "SELECT borrador_md FROM reportes_generados WHERE id = ?",
                           (self.rid,))["borrador_md"]
        self.web.post(f"/panel/reporte/{self.rid}/guardar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"), "cuerpo": original})
        edicion = db.fila(self.con,
                          "SELECT * FROM ediciones_registradas WHERE reporte_id = ?", (self.rid,))
        self.assertEqual(edicion["etiqueta"], "sin_cambios")

    def test_no_guarda_un_reporte_vacio(self):
        self.entrar()
        r = self.web.post(f"/panel/reporte/{self.rid}/guardar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"), "cuerpo": "   "})
        self.assertEqual(r.status_code, 400)
        fila = db.fila(self.con, "SELECT * FROM reportes_generados WHERE id = ?", (self.rid,))
        self.assertEqual(fila["estado"], "borrador")

    def test_el_preview_del_correo_usa_el_mismo_render_que_el_envio(self):
        self.entrar()
        r = self.web.get(f"/panel/reporte/{self.rid}/correo")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Monge", r.get_data(as_text=True))

    def test_un_reporte_que_no_existe_da_404(self):
        self.entrar()
        self.assertEqual(self.web.get("/panel/reporte/9999").status_code, 404)


class TestEnviar(CasoPanel):
    def test_no_envia_un_borrador_sin_revisar(self):
        # El seguro está en enviar_reporte(), y el panel no lo puede puentear.
        self.entrar()
        r = self.web.post(f"/panel/reporte/{self.rid}/enviar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"),
            "confirmacion": "Gimnasio Fuerza Tica"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("borrador", r.get_data(as_text=True))

    def test_hay_que_escribir_el_nombre_del_cliente(self):
        # Un clic de más contra un error que no se deshace: el correo ya salió.
        self.entrar()
        self._revisar()
        r = self.web.post(f"/panel/reporte/{self.rid}/enviar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"), "confirmacion": "cualquier cosa"})
        self.assertEqual(r.status_code, 400)
        fila = db.fila(self.con, "SELECT * FROM reportes_generados WHERE id = ?", (self.rid,))
        self.assertEqual(fila["estado"], "revisado")

    def test_simular_no_manda_nada_y_no_cambia_el_estado(self):
        self.entrar()
        self._revisar()
        config.DIR_SALIDA = Path(self.tmp.name) / "salida"
        r = self.web.post(f"/panel/reporte/{self.rid}/enviar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"), "simular": "1",
            "confirmacion": "gimnasio fuerza tica"})
        self.assertEqual(r.status_code, 200)
        fila = db.fila(self.con, "SELECT * FROM reportes_generados WHERE id = ?", (self.rid,))
        self.assertEqual(fila["estado"], "revisado")
        self.assertIsNone(fila["enviado_en"])

    def test_un_reporte_ya_enviado_no_se_manda_otra_vez(self):
        self.entrar()
        db.actualizar(self.con, "reportes_generados", self.rid,
                      {"estado": "enviado", "enviado_en": "2026-01-08 10:00",
                       "final_md": "lo que salió"})
        self.con.commit()
        r = self.web.post(f"/panel/reporte/{self.rid}/enviar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"),
            "confirmacion": "Gimnasio Fuerza Tica"})
        self.assertEqual(r.status_code, 409)

    def _revisar(self) -> None:
        self.web.post(f"/panel/reporte/{self.rid}/guardar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"),
            "cuerpo": "## Resumen\n\nMi versión revisada."})


class TestDescartar(CasoPanel):
    def test_descartar_exige_motivo(self):
        self.entrar()
        r = self.web.post(f"/panel/reporte/{self.rid}/descartar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"), "motivo": "  "})
        self.assertEqual(r.status_code, 400)
        fila = db.fila(self.con, "SELECT * FROM reportes_generados WHERE id = ?", (self.rid,))
        self.assertEqual(fila["estado"], "borrador")

    def test_descartar_guarda_el_motivo(self):
        self.entrar()
        r = self.web.post(f"/panel/reporte/{self.rid}/descartar", data={
            "csrf": self.token(f"/panel/reporte/{self.rid}"),
            "motivo": "la recolección no trajo a Monge"})
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM reportes_generados WHERE id = ?", (self.rid,))
        self.assertEqual(fila["estado"], "descartado")
        self.assertEqual(db.valor(fila, "motivo_descarte"), "la recolección no trajo a Monge")

    def test_no_se_descarta_algo_que_el_cliente_ya_recibio(self):
        self.entrar()
        db.actualizar(self.con, "reportes_generados", self.rid,
                      {"estado": "enviado", "enviado_en": "2026-01-08 10:00"})
        self.con.commit()
        r = self.web.post(f"/panel/reporte/{self.rid}/descartar", data={
            "csrf": self.token("/panel/"), "motivo": "me arrepentí"})
        self.assertEqual(r.status_code, 409)


class TestGasto(CasoPanel):
    def test_suma_la_IA_y_la_recoleccion_por_separado(self):
        # Son dos cosas distintas: la recolección se paga por anuncio y la IA
        # por token. Sumarlas en un solo número esconde cuál se fue de precio.
        db.insertar(self.con, "uso_ia", {
            "tarea": "reporte", "proveedor": "gemini", "modelo": "flash",
            "tokens_entrada": 5000, "tokens_salida": 900, "costo_usd": 0.02})
        db.insertar(self.con, "corridas_recoleccion", {
            "estado": "ok", "fuente": "apify", "disparada_por": "cron", "costo_usd": 0.15})
        self.con.commit()
        self.entrar()
        texto = self.web.get("/panel/gasto").get_data(as_text=True)
        self.assertIn("0.0200", texto)
        self.assertIn("0.1500", texto)
        self.assertIn("0.1700", texto)   # el total
        self.assertIn("gemini", texto)

    def test_sin_datos_no_se_rompe(self):
        self.entrar()
        self.assertEqual(self.web.get("/panel/gasto").status_code, 200)


class TestIdentidad(CasoPanel):
    """El panel es la tercera cara del producto y usa la misma paleta.

    No lo ve el cliente, pero es lo que uno mira todas las semanas: si se le
    cuela un hexadecimal que nadie decidio, la identidad deja de ser una sola
    y nadie se entera. La regla es la misma que en la web y en el correo.
    """

    def test_todas_las_pantallas_usan_solo_la_paleta_de_marca(self):
        from pulserival import marca

        self.entrar()
        for ruta in ("/panel/", f"/panel/reporte/{self.rid}", "/panel/gasto"):
            with self.subTest(ruta=ruta):
                html = self.web.get(ruta).get_data(as_text=True)
                self.assertEqual(marca.fuera_de_paleta(html), [])

    def test_la_pantalla_de_entrar_tambien(self):
        from pulserival import marca

        html = self.web.get("/panel/entrar").get_data(as_text=True)
        self.assertEqual(marca.fuera_de_paleta(html), [])


if __name__ == "__main__":
    unittest.main()
