"""La capa web: landing, alta y cobro simulado. El webhook de Paddle tiene
su propio archivo: tests/test_paddle.py.

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
            "email": "ana@tornillo.test", "plan": "autoservicio", "ciclo": "mensual",
            "industria": "ferreterías", "contexto": "Tres sucursales.",
            "comp1": "EPA", "fb1": "facebook.com/EPACostaRica", "web1": "epa.cr",
            "acepto": "si",
        }
        datos.update(cambios)
        return self.cliente_web.post("/alta", data=datos)


class TestLandingServida(CasoWeb):
    def test_la_landing_carga(self):
        r = self.cliente_web.get("/")
        self.assertEqual(r.status_code, 200)

    def test_la_portada_no_lleva_formulario(self):
        # El formulario se mudó a /aplicar: la portada explica, la otra toma
        # los datos.
        texto = self.cliente_web.get("/").get_data(as_text=True)
        self.assertNotIn("<form", texto)
        self.assertIn("/aplicar", texto)

    def test_un_enlace_viejo_con_ciclo_en_la_url_sigue_cargando(self):
        # La portada mandaba ?ciclo=anual cuando el anual se elegía ahí. Ahora
        # se elige en el checkout, pero un enlace viejo compartido por ahí
        # tiene que seguir llevando al formulario.
        r = self.cliente_web.get("/aplicar?ciclo=anual")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('name="ciclo"', r.get_data(as_text=True))

    def test_el_checkout_ofrece_pagar_el_anio_con_su_ahorro(self):
        r = self.alta()
        h = self.cliente_web.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn('name="ciclo" value="mensual"', h)
        self.assertIn('name="ciclo" value="anual"', h)
        self.assertIn("Ahorra $60", h)
        self.assertIn("Le queda en $20 al mes", h)

    def test_el_checkout_no_preselecciona_el_anual(self):
        # Elegir pagar el año tiene que ser una decisión, no algo que se pasó
        # por alto: queda marcado el mensual, que es lo que vio en la portada.
        r = self.alta()
        h = self.cliente_web.get(r.headers["Location"]).get_data(as_text=True)
        self.assertRegex(h, r'value="mensual"[^>]*checked')
        self.assertNotRegex(h, r'value="anual"[^>]*checked')

    def test_confirmar_con_el_anual_lo_anota(self):
        r = self.alta()
        cid = int(r.headers["Location"].split("/checkout/")[1].split("?")[0])
        firma = r.headers["Location"].split("t=")[1]
        self.cliente_web.post(f"/checkout/{cid}/confirmar", data={"t": firma, "ciclo": "anual"})
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["ciclo_facturacion"], "anual")
        self.assertEqual(fila["estado_suscripcion"], "activa")

    def test_confirmar_con_un_ciclo_inventado_no_lo_anota(self):
        r = self.alta()
        cid = int(r.headers["Location"].split("/checkout/")[1].split("?")[0])
        firma = r.headers["Location"].split("t=")[1]
        self.cliente_web.post(f"/checkout/{cid}/confirmar",
                              data={"t": firma, "ciclo": "trimestral"})
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["ciclo_facturacion"], "mensual")

    def test_un_plan_que_no_se_vende_en_la_web_no_se_puede_contratar(self):
        # El POST lo arma cualquiera: sin este control, alguien se da de
        # alta en el Custom o en la prueba gratis que ya no se ofrece.
        for plan in ("custom", "prueba", "semanal"):
            with self.subTest(plan=plan):
                r = self.alta(plan=plan)
                self.assertEqual(r.status_code, 400)
        self.assertEqual(db.filas(self.con, "SELECT * FROM clientes"), [])

    def test_no_acepta_mas_competidores_que_los_del_plan(self):
        extra = {f"comp{n}": f"Competidor {n}" for n in range(1, 6)}
        extra.update({f"web{n}": f"c{n}.cr" for n in range(1, 6)})
        r = self.alta(**extra)
        self.assertEqual(r.status_code, 400)
        self.assertIn("hasta 4", r.get_data(as_text=True))

    def test_el_alta_guarda_el_ciclo_elegido(self):
        self.alta(ciclo="anual")
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertEqual(fila["ciclo_facturacion"], "anual")
        self.assertEqual(fila["plan"], "autoservicio")

    def test_un_plan_inexistente_en_la_url_no_rompe(self):
        r = self.cliente_web.get("/aplicar?plan=inventado")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<form", r.get_data(as_text=True))

    def test_en_modo_servidor_el_formulario_no_usa_whatsapp(self):
        # La misma plantilla sirve para los dos modos. Si el formulario
        # siguiera armando el mensaje de WhatsApp, el alta no llegaría nunca
        # a la base y nadie se daría cuenta hasta revisar por qué no hay
        # clientes nuevos.
        h = self.cliente_web.get("/aplicar").get_data(as_text=True)
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

    def test_un_alta_sin_competidores_se_acepta(self):
        # Cambió a propósito. La página ofrece deducir los competidores cuando
        # el cliente no los indica, así que el alta entra sin ninguno. Lo que
        # NO se puede es activarla así: eso lo prueba el test de abajo.
        r = self.alta(comp1="", fb1="", web1="")
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertEqual(db.competidores_de(self.con, int(fila["id"])), [])

    def test_la_encuesta_de_origen_llega_hasta_la_base(self):
        # Existe para decidir donde poner el tiempo. Si el formulario la
        # pregunta y la respuesta se pierde en el camino, la pregunta es
        # solo un paso mas que llenar.
        r = self.alta(origen="Alguien me lo recomendó")
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertEqual(fila["origen"], "Alguien me lo recomendó")

    def test_con_otro_vale_lo_que_escribio_y_no_la_etiqueta(self):
        # "Otro" a secas no dice donde poner el tiempo.
        r = self.alta(origen="otro", origen_otro="Un grupo de WhatsApp del gremio")
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertEqual(fila["origen"], "Un grupo de WhatsApp del gremio")

    def test_sin_responder_la_encuesta_el_alta_entra_igual(self):
        # Es opcional: bloquear por una pregunta de marketing seria cambiar
        # un cliente por un dato.
        r = self.alta(origen="", origen_otro="")
        self.assertEqual(r.status_code, 302)
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        self.assertIsNone(fila["origen"])

    def test_un_cliente_sin_competidores_NO_se_puede_activar(self):
        # Activarlo generaría un reporte vacío en la corrida siguiente, con su
        # costo de IA, y el cliente recibiría un correo que no dice nada.
        from pulserival import altas

        self.alta(comp1="", fb1="", web1="")
        fila = db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
        with self.assertRaises(altas.AltaInvalida) as e:
            altas.activar(self.con, int(fila["id"]))
        self.assertIn("competidores", str(e.exception))

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
    def _dar_de_alta(self) -> tuple[int, str]:
        """El id del cliente y la URL firmada a la que lo manda el alta."""
        r = self.alta()
        cid = int(db.fila(self.con, "SELECT id FROM clientes ORDER BY id DESC")["id"])
        return cid, r.headers["Location"]

    def _firma(self, url: str) -> str:
        return url.split("t=", 1)[1]

    def test_el_checkout_avisa_que_es_simulado(self):
        # Si no lo dijera, alguien podría creer que cobró de verdad.
        _, url = self._dar_de_alta()
        h = self.cliente_web.get(url).get_data(as_text=True)
        self.assertIn("Cobro simulado", h)

    def test_confirmar_activa_la_suscripcion(self):
        cid, url = self._dar_de_alta()
        self.cliente_web.post(f"/checkout/{cid}/confirmar", data={"t": self._firma(url)})
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["estado_suscripcion"], "activa")
        self.assertEqual(fila["pago_referencia"], "SIMULADO")

    def test_con_cobro_real_el_navegador_no_puede_activar(self):
        # Con Paddle conectado, quien activa es su webhook. Si este POST
        # siguiera funcionando, cualquiera se activaría solo escribiendo la
        # URL a mano.
        import os

        cid, url = self._dar_de_alta()
        os.environ["PULSERIVAL_COBRO"] = "paddle"
        try:
            r = self.cliente_web.post(f"/checkout/{cid}/confirmar",
                                      data={"t": self._firma(url)})
            self.assertEqual(r.status_code, 403)
            fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
            self.assertEqual(fila["estado_suscripcion"], "pendiente_pago")
        finally:
            os.environ["PULSERIVAL_COBRO"] = "simulado"

    def test_un_cliente_que_no_existe_da_404(self):
        self.assertEqual(self.cliente_web.get("/checkout/9999").status_code, 404)

    def test_sin_la_firma_no_se_ve_el_checkout_de_nadie(self):
        # Antes era /checkout/1, /checkout/2...: cambiando el número se veían
        # el correo y los competidores de otro cliente. 404 y no 403, para no
        # confirmar que el id existe.
        cid, url = self._dar_de_alta()
        for ruta in (f"/checkout/{cid}", f"/checkout/{cid}?t=cualquiera",
                     f"/gracias/{cid}", f"/gracias/{cid}?t=cualquiera"):
            with self.subTest(ruta=ruta):
                r = self.cliente_web.get(ruta)
                self.assertEqual(r.status_code, 404)
                self.assertNotIn("ana@tornillo.test", r.get_data(as_text=True))

    def test_la_firma_de_un_cliente_no_abre_el_de_otro(self):
        cid, url = self._dar_de_alta()
        self.alta(empresa="Otra Ferretería", email="otro@ejemplo.test")
        otro = int(db.fila(self.con, "SELECT id FROM clientes ORDER BY id DESC")["id"])
        r = self.cliente_web.get(f"/checkout/{otro}?t={self._firma(url)}")
        self.assertEqual(r.status_code, 404)


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


class TestDiscoPersistente(CasoWeb):
    """`/salud` tiene que avisar si la base NO está en un disco que persiste.

    Es la falla más cara y la más silenciosa que puede tener el servidor: con
    el volumen montado en otra ruta, SQLite escribe igual —en el sistema de
    archivos del contenedor— y todo anda perfecto hasta el deploy siguiente,
    que se lleva los clientes y el historial de anuncios. Ese historial no se
    recupera: las plataformas solo muestran lo que está activo hoy.
    """

    def setUp(self):
        super().setUp()
        import os

        for v in ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_NAME", "FLY_APP_NAME"):
            os.environ.pop(v, None)

    def _disco(self):
        return self.cliente_web.get("/salud").get_json()["disco"]

    def test_fuera_de_un_hosting_no_alarma(self):
        # En la máquina local la base no está en ningún disco montado y eso es
        # lo normal: un aviso acá sería ruido que enseña a ignorar los avisos.
        self.assertNotIn("ATENCIÓN", self._disco())

    def test_en_un_hosting_sin_disco_montado_avisa_fuerte(self):
        import os

        os.environ["RAILWAY_ENVIRONMENT"] = "production"
        self.addCleanup(lambda: os.environ.pop("RAILWAY_ENVIRONMENT", None))
        aviso = self._disco()
        self.assertIn("ATENCIÓN", aviso)
        self.assertIn("se borra", aviso)
        # Y dice qué revisar, no solo que algo está mal.
        self.assertIn(str(self.ruta.parent), aviso)

    def test_lo_detecta_tambien_en_fly(self):
        import os

        os.environ["FLY_APP_NAME"] = "pulserival"
        self.addCleanup(lambda: os.environ.pop("FLY_APP_NAME", None))
        self.assertIn("ATENCIÓN", self._disco())

    def test_con_disco_montado_lo_confirma(self):
        import os

        os.environ["RAILWAY_ENVIRONMENT"] = "production"
        self.addCleanup(lambda: os.environ.pop("RAILWAY_ENVIRONMENT", None))
        from pulserival.web.app import crear_app

        # /dev/shm es un punto de montaje real en Linux: sirve de disco de
        # mentira para comprobar que la detección distingue los dos casos.
        app = crear_app("/dev/shm/pulserival-prueba.db")
        app.config["TESTING"] = True
        disco = app.test_client().get("/salud").get_json()["disco"]
        self.assertIn("sobrevive", disco)
        self.assertNotIn("ATENCIÓN", disco)

    def test_no_rompe_salud(self):
        # El chequeo es informativo: si fallara, no puede tumbar /salud, que es
        # lo que el hosting usa para decidir si reinicia el contenedor.
        self.assertTrue(self.cliente_web.get("/salud").get_json()["ok"])


class TestCorreoDeSoporte(CasoWeb):
    def test_el_checkout_dice_a_donde_escribir(self):
        r = self.alta(comp1="", fb1="", web1="")
        h = self.cliente_web.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn('href="mailto:soporte@pulserival.com"', h)

    def test_un_plan_que_no_se_vende_en_la_web_dice_a_donde_escribir(self):
        r = self.alta(plan="custom")
        self.assertEqual(r.status_code, 400)
        self.assertIn("soporte@pulserival.com", r.get_data(as_text=True))
