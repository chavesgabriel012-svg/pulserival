"""El webhook de Paddle: lo único que activa o corta a un cliente que paga.

Los eventos de acá tienen la forma que documenta Paddle (developer.paddle.com,
webhooks): event_id, event_type, occurred_at, notification_id y data con la
entidad. Se mandan firmados por la ruta de verdad, igual que llegarían.
"""
from __future__ import annotations

import json
import os
import time

from pulserival import db, pagos
from tests.base import CasoBase

SECRETO_WEBHOOK = "pdl_ntfset_secreto_de_prueba"
SECRETO_APP = "secreto-de-la-app-para-pruebas"


class CasoPaddle(CasoBase):
    def setUp(self):
        super().setUp()
        previos = {k: os.environ.get(k) for k in
                   ("PADDLE_WEBHOOK_SECRET", "PULSERIVAL_SECRET", "PULSERIVAL_COBRO")}
        os.environ["PADDLE_WEBHOOK_SECRET"] = SECRETO_WEBHOOK
        os.environ["PULSERIVAL_SECRET"] = SECRETO_APP
        os.environ["PULSERIVAL_COBRO"] = "paddle"

        def restaurar():
            for k, v in previos.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(restaurar)
        from pulserival.web.app import crear_app

        self.app = crear_app(str(self.ruta))
        self.app.config["TESTING"] = True
        self.web = self.app.test_client()
        self.n = 0

        # Un alta de la web: plan de autoservicio, pago mensual, esperando el pago.
        self.cid = self.cliente(plan="autoservicio", periodicidad="mensual",
                                ciclo_facturacion="mensual",
                                estado_suscripcion="pendiente_pago")
        self.competidor(self.cid)

    # ── eventos ──
    def evento(self, tipo: str, datos: dict, ocurrido: str = "2026-10-19T10:00:00.000000Z",
               event_id: str | None = None) -> dict:
        self.n += 1
        return {"event_id": event_id or f"evt_{self.n:04d}", "event_type": tipo,
                "occurred_at": ocurrido, "notification_id": f"ntf_{self.n:04d}",
                "data": datos}

    def custom_data(self, cid: int | None = None) -> dict:
        cid = self.cid if cid is None else cid
        return {"cliente_id": str(cid), "firma": pagos.firma_cliente(cid)}

    def suscripcion(self, estado: str = "active", intervalo: str = "month", **extra) -> dict:
        datos = {"id": "sub_01", "status": estado, "customer_id": "ctm_01",
                 "billing_cycle": {"interval": intervalo, "frequency": 1},
                 "custom_data": self.custom_data(),
                 "items": [{"price": {"id": "pri_mensual"}}]}
        datos.update(extra)
        return datos

    def transaccion(self, estado: str = "completed", **extra) -> dict:
        datos = {"id": "txn_01", "status": estado, "subscription_id": "sub_01",
                 "customer_id": "ctm_01", "custom_data": self.custom_data(),
                 "items": [{"price": {"id": "pri_mensual",
                                      "billing_cycle": {"interval": "month", "frequency": 1}}}]}
        datos.update(extra)
        return datos

    def enviar(self, evento: dict, secreto: str = SECRETO_WEBHOOK, firma: str | None = None):
        cuerpo = json.dumps(evento).encode()
        cabecera = firma if firma is not None else pagos.firmar(cuerpo, secreto)
        return self.web.post("/webhooks/paddle", data=cuerpo,
                             headers={"Paddle-Signature": cabecera,
                                      "Content-Type": "application/json"})

    def fila(self, cid: int | None = None):
        return db.fila(self.con, "SELECT * FROM clientes WHERE id = ?",
                       (self.cid if cid is None else cid,))


class TestPagoExitoso(CasoPaddle):
    def test_el_primer_pago_activa_y_guarda_la_suscripcion(self):
        r = self.enviar(self.evento("transaction.completed", self.transaccion()))
        self.assertEqual(r.status_code, 200)
        fila = self.fila()
        self.assertEqual(fila["estado_suscripcion"], "activa")
        self.assertEqual(fila["paddle_suscripcion_id"], "sub_01")
        self.assertEqual(fila["paddle_cliente_id"], "ctm_01")
        self.assertEqual(fila["paddle_transaccion_id"], "txn_01")
        self.assertEqual(fila["pago_proveedor"], "paddle")

    def test_la_secuencia_tipica_del_checkout_deja_al_cliente_activo(self):
        # transaction.paid -> subscription.created -> transaction.completed
        # es lo típico según Paddle, no lo garantizado.
        self.enviar(self.evento("transaction.paid", self.transaccion("paid"),
                                "2026-10-19T10:00:01Z"))
        self.enviar(self.evento("subscription.created",
                                self.suscripcion(transaction_id="txn_01"),
                                "2026-10-19T10:00:02Z"))
        self.enviar(self.evento("transaction.completed", self.transaccion(),
                                "2026-10-19T10:00:03Z"))
        fila = self.fila()
        self.assertEqual(fila["estado_suscripcion"], "activa")
        self.assertEqual(fila["paddle_estado"], "active")

    def test_el_anual_queda_registrado_como_anual(self):
        self.enviar(self.evento("subscription.created", self.suscripcion(intervalo="year")))
        self.assertEqual(self.fila()["ciclo_facturacion"], "anual")

    def test_un_cliente_desactivado_por_aplicar_config_vuelve_a_reportarse_al_pagar(self):
        # `aplicar-config` desactiva a los que no están en clientes.yaml, y los
        # que entran por la web no están ahí. Quien paga se reporta igual.
        db.actualizar(self.con, "clientes", self.cid, {"activo": 0})
        self.con.commit()
        self.enviar(self.evento("transaction.completed", self.transaccion()))
        self.assertEqual(self.fila()["activo"], 1)

    def test_la_renovacion_que_entra_despues_de_un_fallo_reactiva(self):
        self.enviar(self.evento("subscription.created", self.suscripcion(), "2026-10-01T10:00:00Z"))
        self.enviar(self.evento("subscription.past_due", self.suscripcion("past_due"),
                                "2026-11-01T10:00:00Z"))
        self.assertEqual(self.fila()["estado_suscripcion"], "vencida")
        self.enviar(self.evento("transaction.completed",
                                self.transaccion(id="txn_02", origin="subscription_recurring"),
                                "2026-11-03T10:00:00Z"))
        self.assertEqual(self.fila()["estado_suscripcion"], "activa")


class TestFalloDeCobro(CasoPaddle):
    def test_una_renovacion_que_falla_deja_de_reportar(self):
        # past_due: Paddle está reintentando. Mientras tanto no se recolecta
        # ni se reporta; si el cobro entra, Paddle avisa y vuelve a activa.
        self.enviar(self.evento("subscription.created", self.suscripcion(), "2026-10-01T10:00:00Z"))
        self.enviar(self.evento("subscription.past_due", self.suscripcion("past_due"),
                                "2026-11-01T10:00:00Z"))
        fila = self.fila()
        self.assertEqual(fila["estado_suscripcion"], "vencida")
        self.assertEqual(fila["paddle_estado"], "past_due")
        from pulserival import planes
        self.assertFalse(planes.reporta(fila))

    def test_una_tarjeta_rechazada_en_el_checkout_no_cambia_nada(self):
        # La persona reintenta con otra tarjeta. No es un cliente "vencido":
        # nunca llegó a pagar.
        r = self.enviar(self.evento("transaction.payment_failed",
                                    self.transaccion("ready", subscription_id=None)))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.fila()["estado_suscripcion"], "pendiente_pago")
        evento = db.fila(self.con, "SELECT * FROM eventos_pago")
        self.assertEqual(evento["resultado"], "sin_efecto")

    def test_un_fallo_no_pisa_a_una_suscripcion_activa(self):
        self.enviar(self.evento("transaction.completed", self.transaccion()))
        self.enviar(self.evento("transaction.payment_failed", self.transaccion(id="txn_99"),
                                "2026-10-20T10:00:00Z"))
        self.assertEqual(self.fila()["estado_suscripcion"], "activa")


class TestCancelacion(CasoPaddle):
    def test_cancelada_deja_de_reportar(self):
        self.enviar(self.evento("subscription.created", self.suscripcion(), "2026-10-01T10:00:00Z"))
        self.enviar(self.evento("subscription.canceled", self.suscripcion("canceled"),
                                "2026-12-01T10:00:00Z"))
        fila = self.fila()
        self.assertEqual(fila["estado_suscripcion"], "cancelada")
        self.assertEqual(fila["paddle_estado"], "canceled")
        from pulserival import planes
        self.assertFalse(planes.reporta(fila))

    def test_una_cancelacion_programada_sigue_activa_hasta_que_ocurre(self):
        # Quien cancela "al final del periodo" ya pagó ese periodo: Paddle lo
        # deja active con un scheduled_change, y recién después manda canceled.
        self.enviar(self.evento("subscription.created", self.suscripcion(), "2026-10-01T10:00:00Z"))
        self.enviar(self.evento("subscription.updated", self.suscripcion(
            scheduled_change={"action": "cancel", "effective_at": "2026-11-01T00:00:00Z"}),
            "2026-10-15T10:00:00Z"))
        self.assertEqual(self.fila()["estado_suscripcion"], "activa")

    def test_una_transaccion_suelta_no_reactiva_a_una_cancelada(self):
        self.enviar(self.evento("subscription.canceled", self.suscripcion("canceled"),
                                "2026-12-01T10:00:00Z"))
        self.enviar(self.evento("transaction.completed", self.transaccion(id="txn_05"),
                                "2026-12-02T10:00:00Z"))
        self.assertEqual(self.fila()["estado_suscripcion"], "cancelada")

    def test_la_pausa_tambien_deja_de_reportar(self):
        self.enviar(self.evento("subscription.paused", self.suscripcion("paused")))
        self.assertEqual(self.fila()["estado_suscripcion"], "pausada")


class TestEntregaDePaddle(CasoPaddle):
    """Paddle entrega al menos una vez y sin orden garantizado."""

    def test_el_mismo_evento_dos_veces_se_aplica_una(self):
        evento = self.evento("subscription.canceled", self.suscripcion("canceled"))
        self.assertEqual(self.enviar(evento).status_code, 200)
        db.actualizar(self.con, "clientes", self.cid, {"estado_suscripcion": "activa"})
        self.con.commit()
        r = self.enviar(evento)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["resultado"], "repetido")
        self.assertEqual(self.fila()["estado_suscripcion"], "activa",
                         "la segunda entrega no volvió a aplicarse")
        self.assertEqual(db.fila(self.con, "SELECT COUNT(*) AS n FROM eventos_pago")["n"], 1)

    def test_un_evento_viejo_que_llega_tarde_no_pisa_uno_nuevo(self):
        self.enviar(self.evento("subscription.canceled", self.suscripcion("canceled"),
                                "2026-12-01T10:00:00Z"))
        r = self.enviar(self.evento("subscription.activated", self.suscripcion("active"),
                                    "2026-10-01T10:00:00Z"))
        self.assertEqual(r.get_json()["resultado"], "viejo")
        self.assertEqual(self.fila()["estado_suscripcion"], "cancelada")

    def test_un_active_que_no_cambia_nada_igual_corre_la_marca(self):
        # Si la marca no avanzara, un past_due más viejo que llegue después
        # se aplicaría encima y dejaría vencido a un cliente al día.
        self.enviar(self.evento("subscription.created", self.suscripcion(), "2026-10-01T10:00:00Z"))
        self.enviar(self.evento("subscription.updated", self.suscripcion("active"),
                                "2026-11-03T10:00:00Z"))
        self.enviar(self.evento("subscription.past_due", self.suscripcion("past_due"),
                                "2026-11-01T10:00:00Z"))
        self.assertEqual(self.fila()["estado_suscripcion"], "activa")

    def test_un_evento_sin_cliente_se_guarda_y_responde_200(self):
        # Reintentarlo no lo va a resolver: queda en eventos_pago para mirarlo.
        datos = self.transaccion(id="txn_otro", subscription_id="sub_otro", custom_data=None)
        r = self.enviar(self.evento("transaction.completed", datos))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["resultado"], "sin_cliente")
        guardado = db.fila(self.con, "SELECT * FROM eventos_pago")
        self.assertIsNone(guardado["cliente_id"])
        self.assertIn("txn_otro", guardado["payload"])


class TestFirma(CasoPaddle):
    def test_sin_firma_no_se_aplica(self):
        r = self.enviar(self.evento("transaction.completed", self.transaccion()), firma="")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.fila()["estado_suscripcion"], "pendiente_pago")

    def test_con_otro_secreto_no_se_aplica(self):
        r = self.enviar(self.evento("transaction.completed", self.transaccion()),
                        secreto="otro-secreto")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.fila()["estado_suscripcion"], "pendiente_pago")

    def test_un_cuerpo_cambiado_despues_de_firmar_no_se_aplica(self):
        cuerpo = json.dumps(self.evento("transaction.completed", self.transaccion())).encode()
        cabecera = pagos.firmar(cuerpo, SECRETO_WEBHOOK)
        cambiado = cuerpo.replace(b"completed", b"completed ")
        r = self.web.post("/webhooks/paddle", data=cambiado,
                          headers={"Paddle-Signature": cabecera})
        self.assertEqual(r.status_code, 400)

    def test_una_firma_vieja_no_se_acepta(self):
        cuerpo = json.dumps(self.evento("transaction.completed", self.transaccion())).encode()
        vieja = pagos.firmar(cuerpo, SECRETO_WEBHOOK, ts=int(time.time()) - 3600)
        r = self.web.post("/webhooks/paddle", data=cuerpo, headers={"Paddle-Signature": vieja})
        self.assertEqual(r.status_code, 400)

    def test_mientras_se_rota_el_secreto_vale_cualquiera_de_los_dos(self):
        cuerpo = json.dumps(self.evento("transaction.completed", self.transaccion())).encode()
        ts = int(time.time())
        buena = pagos.firmar(cuerpo, SECRETO_WEBHOOK, ts).split("h1=")[1]
        mala = pagos.firmar(cuerpo, "secreto-viejo", ts).split("h1=")[1]
        r = self.web.post("/webhooks/paddle", data=cuerpo,
                          headers={"Paddle-Signature": f"ts={ts};h1={mala};h1={buena}"})
        self.assertEqual(r.status_code, 200)

    def test_sin_secreto_configurado_responde_503_para_que_paddle_reintente(self):
        os.environ["PADDLE_WEBHOOK_SECRET"] = ""
        r = self.enviar(self.evento("transaction.completed", self.transaccion()))
        self.assertEqual(r.status_code, 503)


class TestDeQuienEsElPago(CasoPaddle):
    def test_custom_data_con_la_firma_de_otro_cliente_no_se_acepta(self):
        # custom_data lo arma el navegador. Sin la firma, cualquiera podría
        # pagar poniendo el id de otro cliente.
        otro = self.cliente(nombre_empresa="Otra", contacto_email="o@x.test",
                            plan="autoservicio", periodicidad="mensual",
                            estado_suscripcion="pendiente_pago")
        falsa = {"cliente_id": str(otro), "firma": pagos.firma_cliente(self.cid)}
        r = self.enviar(self.evento("transaction.completed",
                                    self.transaccion(custom_data=falsa)))
        self.assertEqual(r.get_json()["resultado"], "sin_cliente")
        self.assertEqual(self.fila(otro)["estado_suscripcion"], "pendiente_pago")

    def test_custom_data_no_le_quita_la_suscripcion_a_quien_ya_la_tiene(self):
        self.enviar(self.evento("transaction.completed", self.transaccion()))
        intruso = self.transaccion(id="txn_intruso", subscription_id="sub_intruso")
        r = self.enviar(self.evento("transaction.completed", intruso, "2026-10-20T10:00:00Z"))
        self.assertEqual(r.get_json()["resultado"], "sin_cliente")
        self.assertEqual(self.fila()["paddle_suscripcion_id"], "sub_01")

    def test_un_custom_se_reconoce_por_el_link_de_pago_registrado_a_mano(self):
        # El Custom se cobra con un link armado en el panel de Paddle: no pasa
        # por nuestro checkout ni trae custom_data. Lo reconoce el txn_ que se
        # registró con `cli clientes agregar --paddle-transaccion`.
        agencia = self.cliente(nombre_empresa="Agencia", contacto_email="a@agencia.test",
                               plan="custom", periodicidad="custom", cadencia_dias=2,
                               precio_mensual_usd=300, estado_suscripcion="pendiente_pago",
                               paddle_transaccion_id="txn_agencia")
        self.competidor(agencia, nombre="EPA")
        datos = self.transaccion(id="txn_agencia", subscription_id="sub_agencia",
                                 customer_id="ctm_agencia", custom_data=None)
        r = self.enviar(self.evento("transaction.completed", datos))
        self.assertEqual(r.get_json()["resultado"], "aplicado")
        fila = self.fila(agencia)
        self.assertEqual(fila["estado_suscripcion"], "activa")
        self.assertEqual(fila["paddle_suscripcion_id"], "sub_agencia")
        # El ciclo es del plan de autoservicio; el Custom no lo tiene.
        self.assertIsNone(fila["ciclo_facturacion"])
        # Y su renovación se reconoce por la suscripción, ya sin el link.
        self.enviar(self.evento("subscription.canceled",
                                {"id": "sub_agencia", "status": "canceled",
                                 "customer_id": "ctm_agencia"}, "2026-12-01T10:00:00Z"))
        self.assertEqual(self.fila(agencia)["estado_suscripcion"], "cancelada")


class TestCheckoutConPaddle(CasoPaddle):
    def test_el_checkout_manda_el_precio_del_ciclo_y_el_cliente_firmado(self):
        from unittest import mock

        os.environ["PADDLE_CLIENT_TOKEN"] = "test_token"
        self.addCleanup(os.environ.__setitem__, "PADDLE_CLIENT_TOKEN", "")
        firma = pagos.firma_cliente(self.cid, self.app.secret_key)
        with mock.patch.object(pagos, "precio_paddle", return_value="pri_mensual_sandbox"):
            h = self.web.get(f"/checkout/{self.cid}?t={firma}").get_data(as_text=True)
        self.assertIn("cdn.paddle.com/paddle/v2/paddle.js", h)
        self.assertIn('Paddle.Environment.set("sandbox")', h)
        self.assertIn('"pri_mensual_sandbox"', h)
        self.assertIn(f'"cliente_id": "{self.cid}"', h)
        self.assertIn(firma, h)

    def test_con_la_configuracion_incompleta_no_ofrece_pagar(self):
        # Un checkout que cobra con el webhook roto deja a alguien pagando y
        # sin cuenta. Mejor no mostrar el botón.
        firma = pagos.firma_cliente(self.cid, self.app.secret_key)
        h = self.web.get(f"/checkout/{self.cid}?t={firma}").get_data(as_text=True)
        self.assertNotIn("Paddle.Checkout.open", h)
        self.assertIn("todavía no está disponible", h)

    def test_gracias_no_dice_activa_hasta_que_lo_confirme_el_webhook(self):
        firma = pagos.firma_cliente(self.cid, self.app.secret_key)
        h = self.web.get(f"/gracias/{self.cid}?t={firma}").get_data(as_text=True)
        self.assertIn("Estamos esperando la confirmación", h)
        self.assertNotIn("quedó activa", h)
        self.enviar(self.evento("transaction.completed", self.transaccion()))
        h = self.web.get(f"/gracias/{self.cid}?t={firma}").get_data(as_text=True)
        self.assertIn("quedó activa", h)

    def test_la_pagina_del_link_de_pago_carga_paddle(self):
        # Sin esta página los links de cobro del Custom llevan a un 404.
        h = self.web.get("/pagar?_ptxn=txn_agencia").get_data(as_text=True)
        self.assertIn("cdn.paddle.com/paddle/v2/paddle.js", h)
        self.assertIn("Paddle.Initialize", h)

    def test_salud_avisa_lo_que_falta_para_cobrar(self):
        r = self.web.get("/salud")
        self.assertEqual(r.status_code, 500)
        faltan = " ".join(r.get_json()["falta_configurar"])
        self.assertIn("PADDLE_CLIENT_TOKEN", faltan)
        self.assertIn("precio de Paddle", faltan)


class TestPipelineConPaddle(CasoPaddle):
    def test_quien_pago_sin_competidores_no_recibe_un_reporte_vacio(self):
        # Pagó y nos pidió que investiguemos a la competencia. Generarle el
        # reporte igual daría uno que dice "no hubo movimiento" sin haber
        # mirado a nadie, y cobraría la IA.
        from pulserival import pipeline

        sin = self.cliente(nombre_empresa="Sin competidores", contacto_email="s@x.test",
                           plan="autoservicio", periodicidad="mensual",
                           estado_suscripcion="activa")
        resultado = pipeline.ciclo_completo(self.con, cliente_id=sin, modo="demo")
        omitidos = [r for r in resultado["reportes"] if r.get("omitido")]
        self.assertEqual(len(omitidos), 1)
        self.assertIn("competidores", omitidos[0]["omitido"])
        self.assertEqual(db.filas(self.con, "SELECT * FROM reportes_generados "
                                            "WHERE cliente_id = ?", (sin,)), [])
