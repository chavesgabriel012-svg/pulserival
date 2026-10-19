"""El plan Custom (agencias) y los clientes que pagan por Paddle, del lado de
la operación: cómo se registran a mano y qué no puede pisar `aplicar-config`.

Un Custom no tiene checkout: se cotiza por conversación y se cobra con un link
de pago armado en el panel de Paddle. Lo único que necesita el código es
poder registrarlo con su cadencia, su precio y el id de ese link.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

import yaml

from pulserival import cli, db, sincronizar
from tests.base import CasoBase


class CasoCli(CasoBase):
    def cli(self, *args: str) -> tuple[int, str]:
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
            codigo = cli.main(list(args))
        return codigo, salida.getvalue()

    def ultimo(self):
        return db.fila(self.con, "SELECT * FROM clientes ORDER BY id DESC LIMIT 1")


class TestRegistrarUnCustom(CasoCli):
    def test_queda_con_su_cadencia_su_precio_y_el_link_de_paddle(self):
        codigo, _ = self.cli("clientes", "agregar", "--empresa", "Agencia Norte",
                             "--email", "a@norte.test", "--plan", "custom",
                             "--cadencia-dias", "2", "--precio", "300",
                             "--paddle-transaccion", "txn_01abc")
        self.assertEqual(codigo, 0)
        fila = self.ultimo()
        self.assertEqual(fila["periodicidad"], "custom")
        self.assertEqual(fila["cadencia_dias"], 2)
        self.assertEqual(fila["precio_mensual_usd"], 300)
        self.assertEqual(fila["paddle_transaccion_id"], "txn_01abc")
        self.assertEqual(fila["pago_proveedor"], "paddle")
        self.assertEqual(fila["estado_suscripcion"], "pendiente_pago",
                         "no reporta hasta que Paddle avise que el link se pagó")

    def test_sin_cadencia_no_se_registra(self):
        codigo, salida = self.cli("clientes", "agregar", "--empresa", "Agencia",
                                  "--email", "a@x.test", "--plan", "custom", "--precio", "300")
        self.assertEqual(codigo, 1)
        self.assertIn("cadencia", salida)
        self.assertIsNone(self.ultimo())

    def test_sin_precio_no_se_registra(self):
        # Es lo que define a un Custom, y el panel de Paddle no lo dice.
        codigo, salida = self.cli("clientes", "agregar", "--empresa", "Agencia",
                                  "--email", "a@x.test", "--plan", "custom",
                                  "--cadencia-dias", "2")
        self.assertEqual(codigo, 1)
        self.assertIn("--precio", salida)

    def test_avisa_si_falta_el_link_de_paddle(self):
        # Sin el txn_ el webhook no reconoce el pago y el cliente se queda
        # esperando una activación que no llega.
        _, salida = self.cli("clientes", "agregar", "--empresa", "Agencia",
                             "--email", "a@x.test", "--plan", "custom",
                             "--cadencia-dias", "2", "--precio", "300")
        self.assertIn("--paddle-transaccion", salida)

    def test_avisa_que_una_cadencia_corta_no_se_cumple_todavia(self):
        # El ciclo corre cada 7 días. Vender "cada 2 días" sin la cola de
        # trabajos por cliente es vender algo que el sistema no hace.
        _, salida = self.cli("clientes", "agregar", "--empresa", "Agencia",
                             "--email", "a@x.test", "--plan", "custom",
                             "--cadencia-dias", "2", "--precio", "300",
                             "--paddle-transaccion", "txn_1")
        self.assertIn("PULSERIVAL_CICLO_CADA_DIAS", salida)

    def test_una_cadencia_en_un_plan_que_no_es_custom_se_rechaza(self):
        codigo, salida = self.cli("clientes", "agregar", "--empresa", "X", "--email",
                                  "x@x.test", "--plan", "autoservicio", "--cadencia-dias", "5")
        self.assertEqual(codigo, 1)
        self.assertIn("periodicidad custom", salida)

    def test_el_link_se_puede_cargar_despues(self):
        self.cli("clientes", "agregar", "--empresa", "Agencia", "--email", "a@x.test",
                 "--plan", "custom", "--cadencia-dias", "3", "--precio", "500")
        cid = self.ultimo()["id"]
        codigo, _ = self.cli("clientes", "editar", "--id", str(cid),
                             "--paddle-transaccion", "txn_tarde")
        self.assertEqual(codigo, 0)
        self.assertEqual(self.ultimo()["paddle_transaccion_id"], "txn_tarde")

    def test_salir_de_custom_borra_la_cadencia(self):
        self.cli("clientes", "agregar", "--empresa", "Agencia", "--email", "a@x.test",
                 "--plan", "custom", "--cadencia-dias", "3", "--precio", "500")
        cid = self.ultimo()["id"]
        codigo, _ = self.cli("clientes", "editar", "--id", str(cid), "--periodicidad", "mensual")
        self.assertEqual(codigo, 0)
        self.assertIsNone(self.ultimo()["cadencia_dias"])


class TestAutoservicioPorCli(CasoCli):
    def test_el_anual_queda_anual_y_cobra_20_por_mes(self):
        from pulserival import planes

        self.cli("clientes", "agregar", "--empresa", "Ferretería", "--email", "f@x.test",
                 "--ciclo", "anual")
        fila = self.ultimo()
        self.assertEqual(fila["plan"], "autoservicio")
        self.assertEqual(fila["ciclo_facturacion"], "anual")
        self.assertEqual(planes.precio_usd(fila), 20.0)

    def test_el_mensual_cobra_25(self):
        from pulserival import planes

        self.cli("clientes", "agregar", "--empresa", "Ferretería", "--email", "f@x.test")
        fila = self.ultimo()
        self.assertEqual(fila["ciclo_facturacion"], "mensual")
        self.assertEqual(planes.precio_usd(fila), 25.0)


class TestAplicarConfigNoPisaAPaddle(CasoBase):
    """clientes.yaml lo escribe una persona una vez; Paddle sabe si la tarjeta
    de este mes pasó. Con los dos escribiendo `estado_suscripcion`, el que
    tiene la verdad es Paddle."""

    def aplicar(self, entradas: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "clientes.yaml"
            ruta.write_text(yaml.safe_dump({"clientes": entradas}, allow_unicode=True),
                            encoding="utf-8")
            resumen = sincronizar.aplicar(self.con, ruta)
        self.con.commit()
        return resumen

    def test_no_reactiva_a_quien_paddle_dio_por_vencido(self):
        cid = self.cliente(clave="roble", plan="autoservicio", periodicidad="mensual",
                           estado_suscripcion="vencida", paddle_suscripcion_id="sub_1")
        self.competidor(cid)
        self.aplicar([{"clave": "roble", "empresa": "Ferretería El Roble",
                       "contacto_email": "a@b.test", "periodicidad": "mensual",
                       "estado_suscripcion": "activa",
                       "competidores": [{"nombre": "EPA", "google_dominio": "epa.cr"}]}])
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["estado_suscripcion"], "vencida")

    def test_no_desactiva_a_quien_paga_por_paddle_y_no_esta_en_el_yaml(self):
        # Los que entran por la web nunca estuvieron en clientes.yaml.
        cid = self.cliente(clave="web-roble", plan="autoservicio", periodicidad="mensual",
                           estado_suscripcion="activa", paddle_suscripcion_id="sub_2")
        resumen = self.aplicar([])
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["activo"], 1)
        self.assertNotIn("web-roble", resumen["desactivados"])

    def test_a_un_cliente_sin_paddle_lo_sigue_manejando_el_yaml(self):
        cid = self.cliente(clave="viejo", estado_suscripcion="activa")
        self.aplicar([])
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["activo"], 0)

    def test_un_custom_en_el_yaml_necesita_cadencia(self):
        with self.assertRaises(sincronizar.ConfigInvalida):
            sincronizar.validar({"clientes": [
                {"clave": "agencia", "empresa": "Agencia", "contacto_email": "a@x.test",
                 "periodicidad": "custom", "competidores": []}]})

    def test_un_custom_en_el_yaml_con_cadencia_se_carga(self):
        self.aplicar([{"clave": "agencia", "empresa": "Agencia",
                       "contacto_email": "a@x.test", "plan": "custom",
                       "periodicidad": "custom", "cadencia_dias": 2,
                       "competidores": [{"nombre": "EPA", "google_dominio": "epa.cr"}]}])
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE clave = 'agencia'")
        self.assertEqual(fila["periodicidad"], "custom")
        self.assertEqual(fila["cadencia_dias"], 2)
