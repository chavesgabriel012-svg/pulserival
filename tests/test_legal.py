"""Las páginas legales, el consentimiento del formulario y el borrado de datos.

Paddle no aprueba el dominio sin Términos, Política de reembolso y Política
de privacidad públicas, con el nombre legal del vendedor y la frase que lo
nombra como comerciante registrado. La Ley 8968 pide el consentimiento
expreso y por escrito antes de guardar datos personales, y que el titular
pueda pedir que se borren. Esto prueba las tres cosas.
"""
from __future__ import annotations

import contextlib
import copy
import io
import tempfile
from pathlib import Path
from unittest import mock

import yaml

from pulserival import cli, config, db, sincronizar
from pulserival.landing import generador, legal
from tests.base import CasoBase
from tests.test_web import CasoWeb

LEGAL_REAL = yaml.safe_load((config.DIR_CONFIG / "legal.yaml").read_text(encoding="utf-8"))


def legal_con(**responsable) -> dict:
    datos = copy.deepcopy(LEGAL_REAL)
    datos["responsable"].update(responsable)
    return datos


COMPLETO = dict(razon_social="Ana Pérez Mora", identificacion="1-1111-1111",
                direccion="San José, Montes de Oca", correo="hola@pulserival.test")


class TestPaginas(CasoWeb):
    def test_las_tres_responden_sin_login(self):
        for ruta in ("/terminos", "/reembolsos", "/privacidad"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.cliente_web.get(ruta).status_code, 200)

    def test_los_terminos_nombran_a_paddle_como_merchant_of_record(self):
        # La frase exacta que Paddle pide en los Términos.
        h = self.cliente_web.get("/terminos").get_data(as_text=True)
        self.assertIn("Our order process is conducted by our online reseller Paddle.com. "
                      "Paddle.com is the Merchant of Record for all our orders.",
                      " ".join(h.split()))

    def test_la_politica_de_reembolso_no_dice_que_no_hay_reembolsos(self):
        # Paddle rechaza "all sales are final" y "no refunds", y pide una
        # ventana de entre 14 y 90 días.
        h = " ".join(self.cliente_web.get("/reembolsos").get_data(as_text=True).split())
        self.assertIn("Garantía de 14 días", h)
        self.assertNotIn("no hay reembolsos", h.lower())
        dias = int(LEGAL_REAL["numeros"]["dias_garantia"])
        self.assertTrue(14 <= dias <= 90)

    def test_la_privacidad_nombra_a_paddle_entre_los_destinatarios(self):
        h = self.cliente_web.get("/privacidad").get_data(as_text=True)
        self.assertIn("<strong>Paddle</strong>", h)
        self.assertIn("Prodhab", h)

    def test_sin_datos_del_responsable_se_ve_pendiente_y_no_se_inventa(self):
        with mock.patch.object(config, "config_legal", return_value=legal_con(
                razon_social="", identificacion="", direccion="", correo="")):
            h = self.cliente_web.get("/terminos").get_data(as_text=True)
        self.assertIn("[pendiente: la razón social (nombre legal)]", h)
        self.assertIn("Faltan datos para que esta página esté completa", h)

    def test_con_los_datos_cargados_no_queda_nada_pendiente(self):
        with mock.patch.object(config, "config_legal", return_value=legal_con(**COMPLETO)):
            h = self.cliente_web.get("/terminos").get_data(as_text=True)
        self.assertNotIn("pendiente:", h)
        self.assertIn("Ana Pérez Mora", h)

    def test_un_dato_con_html_se_escapa(self):
        with mock.patch.object(config, "config_legal", return_value=legal_con(
                **{**COMPLETO, "razon_social": "<script>alert(1)</script>"})):
            h = self.cliente_web.get("/privacidad").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;", h)

    def test_el_pie_de_cada_pagina_enlaza_las_tres(self):
        # Paddle pide que se lleguen navegando, no solo sabiendo la URL.
        for ruta in ("/", "/aplicar", "/terminos"):
            with self.subTest(ruta=ruta):
                h = self.cliente_web.get(ruta).get_data(as_text=True)
                for destino in ('href="/terminos"', 'href="/reembolsos"',
                                'href="/privacidad"'):
                    self.assertIn(destino, h)

    def test_cli_landing_avisa_lo_que_falta(self):
        with mock.patch.object(config, "config_legal", return_value=legal_con(
                razon_social="", identificacion="", direccion="", correo="")):
            faltan = generador.pendientes()
        self.assertTrue(any("razón social" in f for f in faltan))


class TestSaludEnProduccion(CasoBase):
    def test_en_produccion_salud_avisa_los_datos_legales_que_faltan(self):
        from pulserival import pagos

        vacio = legal_con(razon_social="", identificacion="", direccion="", correo="")
        with mock.patch.object(config, "config_legal", return_value=vacio), \
             mock.patch.dict("os.environ", {"PADDLE_ENTORNO": "production"}):
            faltan = pagos.pendientes()
        self.assertTrue(any("legal.yaml" in f for f in faltan))

    def test_en_sandbox_no_lo_exige(self):
        # El sandbox no pide aprobación del dominio: exigirlo trabaría las
        # pruebas de cobro sin necesidad.
        from pulserival import pagos

        vacio = legal_con(razon_social="", identificacion="", direccion="", correo="")
        with mock.patch.object(config, "config_legal", return_value=vacio), \
             mock.patch.dict("os.environ", {"PADDLE_ENTORNO": "sandbox"}):
            faltan = pagos.pendientes()
        self.assertFalse(any("legal.yaml" in f for f in faltan))


class TestEstatico(CasoBase):
    def test_escribe_las_tres_con_enlaces_a_archivos(self):
        with tempfile.TemporaryDirectory() as tmp:
            generador.construir(Path(tmp))
            h = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            self.assertTrue((Path(tmp) / "privacidad.html").exists())
        self.assertIn('href="privacidad.html"', h)
        self.assertNotIn('href="/privacidad"', h)


class TestConsentimiento(CasoWeb):
    def test_sin_aceptar_no_se_guarda_nada(self):
        r = self.alta(acepto="")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(db.fila(self.con, "SELECT id FROM clientes"))

    def test_al_aceptar_queda_la_constancia(self):
        r = self.alta()
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertEqual(fila["consentimiento_version"], legal.version())
        self.assertTrue(fila["consentimiento_en"])

    def test_el_formulario_lleva_la_casilla_con_los_enlaces(self):
        # Solo la casilla: el detalle de qué datos se guardan está en la
        # Política de privacidad, que la casilla enlaza.
        h = self.cliente_web.get("/aplicar").get_data(as_text=True)
        bloque = h.split('id="bloque-acepto"', 1)[1].split("</div>", 1)[0]
        self.assertIn('name="acepto"', bloque)
        self.assertIn('href="/terminos"', bloque)
        self.assertIn('href="/privacidad"', bloque)

    def test_aplicar_config_no_borra_la_constancia(self):
        # Mover la entrada de config/altas/ a clientes.yaml sin copiar estos
        # campos no puede borrar la única prueba del consentimiento.
        self.alta()
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "clientes.yaml"
            ruta.write_text(yaml.safe_dump({"clientes": [{
                "clave": fila["clave"], "empresa": fila["nombre_empresa"],
                "contacto_email": fila["contacto_email"], "plan": "autoservicio",
                "periodicidad": "mensual", "competidores": [
                    {"nombre": "EPA", "google_dominio": "epa.cr"}]}]},
                allow_unicode=True), encoding="utf-8")
            sincronizar.aplicar(self.con, ruta)
        self.con.commit()
        despues = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (fila["id"],))
        self.assertEqual(despues["consentimiento_version"], fila["consentimiento_version"])


class TestWhatsAppConTodosLosCompetidores(CasoWeb):
    def test_el_mensaje_lee_tantos_competidores_como_casillas_hay(self):
        # Regresión: el guion que arma el mensaje de WhatsApp tenía un 3 fijo
        # y el plan admite 4 competidores. El cuarto se perdía del mensaje
        # sin que nadie se enterara.
        with tempfile.TemporaryDirectory() as tmp:
            generador.construir(Path(tmp))
            h = (Path(tmp) / "aplicar.html").read_text(encoding="utf-8")
        casillas = h.count('name="comp')
        self.assertGreaterEqual(casillas, 4)
        self.assertIn(f"i <= {casillas}; i++", h)


class TestEliminarCliente(CasoBase):
    def cli(self, *args: str) -> tuple[int, str]:
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
            codigo = cli.main(list(args))
        return codigo, salida.getvalue()

    def preparar(self, **extra) -> tuple[int, Path]:
        cid = self.cliente(**extra)
        self.competidor(cid)
        rid = db.insertar(self.con, "reportes_generados", {
            "cliente_id": cid, "periodo_inicio": "2026-09-01", "periodo_fin": "2026-09-30"})
        self.con.commit()
        config.DIR_SALIDA.mkdir(parents=True, exist_ok=True)
        copia = config.DIR_SALIDA / f"2026-09-30-reporte-{rid}.html"
        copia.write_text("reporte", encoding="utf-8")
        return cid, copia

    def test_sin_confirmar_no_borra_nada(self):
        cid, copia = self.preparar()
        codigo, salida = self.cli("clientes", "eliminar", "--id", str(cid))
        self.assertEqual(codigo, 0)
        self.assertIn("No se borró nada", salida)
        self.assertIsNotNone(db.fila(self.con, "SELECT id FROM clientes WHERE id = ?", (cid,)))
        self.assertTrue(copia.exists())

    def test_con_confirmar_borra_todo_lo_suyo(self):
        cid, copia = self.preparar()
        codigo, _ = self.cli("clientes", "eliminar", "--id", str(cid), "--confirmar")
        self.assertEqual(codigo, 0)
        for tabla in ("competidores_seguidos", "reportes_generados"):
            self.assertIsNone(db.fila(self.con, f"SELECT id FROM {tabla} WHERE cliente_id = ?",
                                      (cid,)), tabla)
        self.assertFalse(copia.exists(), "la copia del reporte enviado tiene sus datos")

    def test_no_borra_a_quien_sigue_pagando_en_paddle(self):
        # Borrarlo acá no cancela nada en Paddle: seguiría pagando.
        cid, _ = self.preparar(paddle_suscripcion_id="sub_1", paddle_estado="active")
        codigo, salida = self.cli("clientes", "eliminar", "--id", str(cid), "--confirmar")
        self.assertEqual(codigo, 1)
        self.assertIn("Cancélela primero", salida)
        self.assertIsNotNone(db.fila(self.con, "SELECT id FROM clientes WHERE id = ?", (cid,)))
