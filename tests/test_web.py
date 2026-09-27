"""La capa web: landing, alta y cobro simulado.

Lo que más importa probar acá no es que las páginas carguen, sino que un
formulario público no pueda saltarse los seguros: activarse solo, gastar
scraper sin pagar, o crear un cliente al que después el pipeline no le pueda
reportar.
"""
from __future__ import annotations

import unittest

from pulserival import db
from tests.base import CasoBase


class CasoWeb(CasoBase):
    def setUp(self):
        super().setUp()
        from pulserival.web.app import crear_app

        self.app = crear_app(str(self.ruta))
        self.app.config["TESTING"] = True
        self.cliente_web = self.app.test_client()
        # El límite por IP es global al proceso: sin esto, el test número
        # seis de la clase empieza a recibir 429 por culpa de los anteriores.
        from pulserival.web import app as modulo

        modulo._VISTAS.clear()

    def alta(self, **cambios):
        datos = {
            "empresa": "Ferretería El Tornillo", "contacto": "Ana",
            "email": "ana@tornillo.test", "plan": "semanal",
            "industria": "ferreterías", "contexto": "Tres sucursales.",
            "comp1": "EPA", "fb1": "facebook.com/EPACostaRica", "web1": "epa.cr",
        }
        datos.update(cambios)
        return self.cliente_web.post("/alta", data=datos)


class TestLandingServida(CasoWeb):
    def test_la_landing_carga(self):
        r = self.cliente_web.get("/")
        self.assertEqual(r.status_code, 200)

    def test_en_modo_servidor_el_formulario_no_usa_whatsapp(self):
        # La misma plantilla sirve para los dos modos. Si el formulario
        # siguiera armando el mensaje de WhatsApp, el alta no llegaría nunca
        # a la base y nadie se daría cuenta hasta revisar por qué no hay
        # clientes nuevos.
        h = self.cliente_web.get("/").get_data(as_text=True)
        self.assertIn('action="/alta"', h)
        self.assertNotIn("wa.me", h)

    def test_salud_responde(self):
        datos = self.cliente_web.get("/salud").get_json()
        self.assertTrue(datos["ok"])
        self.assertEqual(datos["cobro"], "simulado")


class TestAlta(CasoWeb):
    def test_crea_el_cliente_y_sus_competidores(self):
        r = self.alta()
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertEqual(fila["nombre_empresa"], "Ferretería El Tornillo")
        comps = db.competidores_de(self.con, int(fila["id"]))
        self.assertEqual(len(comps), 1)

    def test_el_alta_NO_queda_activa(self):
        # Es el seguro central: un formulario público no puede activar una
        # suscripción. Si esto se rompe, cualquiera se da de alta y empieza
        # a gastar Apify sin pagar.
        self.alta()
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertEqual(fila["estado_suscripcion"], "pendiente_pago")

    def test_guarda_el_contexto_del_negocio(self):
        # Es lo que más sube la calidad del reporte (docs/05).
        self.alta(contexto="Compite por servicio, no por precio.")
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertIn("servicio", fila["notas"])

    def test_rechaza_un_competidor_sin_dónde_buscar(self):
        # pipeline._tiene_datos_para() exige al menos un campo por
        # plataforma. Aceptarlo acá sería fallar callado el lunes.
        r = self.alta(comp1="Solo Un Nombre", fb1="", web1="")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(db.filas(self.con, "SELECT * FROM clientes"), [])

    def test_rechaza_sin_competidores(self):
        r = self.alta(comp1="", fb1="", web1="")
        self.assertEqual(r.status_code, 400)

    def test_rechaza_un_correo_invalido(self):
        r = self.alta(email="no-es-un-correo")
        self.assertEqual(r.status_code, 400)

    def test_el_competidor_queda_utilizable_por_el_pipeline(self):
        # No alcanza con guardarlo: tiene que pasar el chequeo que el
        # pipeline hace antes de raspar.
        from pulserival.pipeline import _tiene_datos_para

        self.alta(comp1="Construplaza", fb1="", web1="construplaza.com")
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        comp = dict(db.competidores_de(self.con, int(fila["id"]))[0])
        self.assertTrue(_tiene_datos_para(comp, "google"))

    def test_limita_las_peticiones_por_ip(self):
        for _ in range(5):
            self.alta()
        self.assertEqual(self.alta().status_code, 429)


class TestCobroSimulado(CasoWeb):
    def _dar_de_alta(self):
        self.alta()
        return int(db.fila(self.con, "SELECT id FROM clientes ORDER BY id DESC")["id"])

    def test_el_checkout_avisa_que_es_simulado(self):
        # Si no lo dijera, alguien podría creer que cobró de verdad.
        cid = self._dar_de_alta()
        h = self.cliente_web.get(f"/checkout/{cid}").get_data(as_text=True)
        self.assertIn("Cobro simulado", h)

    def test_confirmar_activa_la_suscripcion(self):
        cid = self._dar_de_alta()
        self.cliente_web.post(f"/checkout/{cid}/confirmar")
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["estado_suscripcion"], "activa")
        self.assertEqual(fila["pago_referencia"], "SIMULADO")

    def test_con_cobro_real_el_navegador_no_puede_activar(self):
        # Con la pasarela conectada, quien activa es su webhook. Si este POST
        # siguiera funcionando, cualquiera se activaría solo escribiendo la
        # URL a mano.
        import os

        cid = self._dar_de_alta()
        os.environ["PULSERIVAL_COBRO"] = "real"
        try:
            r = self.cliente_web.post(f"/checkout/{cid}/confirmar")
            self.assertEqual(r.status_code, 403)
            fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
            self.assertEqual(fila["estado_suscripcion"], "pendiente_pago")
        finally:
            os.environ["PULSERIVAL_COBRO"] = "simulado"

    def test_un_cliente_que_no_existe_da_404(self):
        self.assertEqual(self.cliente_web.get("/checkout/9999").status_code, 404)


class TestVerificacionDeCompetidores(CasoWeb):
    def test_apagada_por_defecto(self):
        # Cada verificación llama al scraper y eso cuesta por anuncio. Un
        # formulario público con esto encendido es una forma de que un
        # desconocido gaste el crédito de Apify.
        from pulserival.web.app import _verificar

        self.assertEqual(_verificar([{"nombre": "X", "google_dominio": "x.com"}]), [])


class TestSinBaseDeDatos(CasoWeb):
    """El mismo formulario cuando el servidor no tiene disco (Vercel).

    El alta se deposita como YAML en el repositorio. Lo que hay que probar es
    que el flujo no le miente al cliente: sin base no hay suscripción activa,
    así que la pantalla final no puede decir que la cuenta quedó activa.
    """

    def setUp(self):
        super().setUp()
        from pulserival.web import deposito as deposito_mod
        from pulserival.web.app import crear_app

        self.deposito = deposito_mod.DepositoGitHub(
            repo="alguien/pulserival", token="ghp_falso")
        self.app = crear_app(str(self.ruta), deposito=self.deposito)
        self.app.config["TESTING"] = True
        self.cliente_web = self.app.test_client()
        from pulserival.web import app as modulo

        modulo._VISTAS.clear()

    def test_el_alta_no_manda_a_un_checkout_que_no_existe(self):
        # Sin base no hay id de cliente. Antes de separar el depósito, el alta
        # redirigía siempre a /checkout/<id> con el id del resultado: acá sería
        # /checkout/None y un 404 justo después de que el cliente cargó todo.
        import unittest.mock as mock

        class Ok:
            status_code = 201
            text = "{}"

        with mock.patch("requests.put", return_value=Ok()):
            r = self.alta()
        self.assertEqual(r.status_code, 200)
        texto = r.get_data(as_text=True)
        self.assertIn("Recibimos su solicitud", texto)
        self.assertNotIn("quedó activa", texto)

    def test_le_dice_que_todavia_no_esta_activa(self):
        import unittest.mock as mock

        class Ok:
            status_code = 201
            text = "{}"

        with mock.patch("requests.put", return_value=Ok()):
            texto = self.alta().get_data(as_text=True)
        self.assertIn("Todavía no está activa", texto)
        self.assertIn("ana@tornillo.test", texto)

    def test_si_el_deposito_falla_el_cliente_se_entera(self):
        # Lo peor que puede pasar acá es tragarse el error: el cliente cree que
        # se registró, nadie tiene sus datos, y no hay forma de saber que pasó.
        import unittest.mock as mock

        class Falla:
            status_code = 403
            text = "sin permiso"

        with mock.patch("requests.put", return_value=Falla()):
            r = self.alta()
        self.assertEqual(r.status_code, 502)
        self.assertIn("Escríbanos", r.get_data(as_text=True))

    def test_salud_avisa_si_falta_configurar_el_deposito(self):
        from pulserival.web import deposito as deposito_mod
        from pulserival.web.app import crear_app

        app = crear_app(str(self.ruta),
                        deposito=deposito_mod.DepositoGitHub(repo="", token=""))
        r = app.test_client().get("/salud")
        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.get_json()["ok"])
        self.assertIn("PULSERIVAL_REPO", " ".join(r.get_json()["falta_configurar"]))

    def test_valida_antes_de_depositar(self):
        import unittest.mock as mock

        with mock.patch("requests.put") as put:
            r = self.alta(email="no-es-un-correo")
        self.assertEqual(r.status_code, 400)
        put.assert_not_called()
