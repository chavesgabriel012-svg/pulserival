"""El cron adentro del proceso web.

Lo que importa probar no es que corra, sino **que no corra de más**. Cada
corrida llama al scraper, que cobra por anuncio. El hilo despierta cada hora:
si decidiera mal, pagaría Apify veinticuatro veces por día en vez de una por
semana.

Lo segundo que importa es lo contrario: que una corrida atrasada no se pierda.
Esa es la razón de que esto no sea un cron.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pulserival import db
from pulserival.web import planificador
from tests.base import CasoBase

AHORA = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)   # lunes, 12:00 UTC


class CasoPlanificador(CasoBase):
    def corrida(self, hace_dias: float = 0, estado: str = "ok",
                ahora: datetime = AHORA) -> int:
        cuando = ahora - timedelta(days=hace_dias)
        cid = db.insertar(self.con, "corridas_recoleccion", {
            "iniciada_en": cuando.strftime("%Y-%m-%d %H:%M:%S"),
            "terminada_en": None if estado == "en_curso"
                            else cuando.strftime("%Y-%m-%d %H:%M:%S"),
            "estado": estado, "fuente": "apify", "disparada_por": "cron"})
        self.con.commit()
        return cid


class TestDecidir(CasoPlanificador):
    def test_sin_ninguna_corrida_NO_arranca_sola(self):
        # Se descubrió corriendo el servidor con el disco vacío: el
        # planificador disparaba un ciclo real —con su gasto de Apify— en el
        # momento del arranque. Un deploy no puede gastar plata por su cuenta.
        d = planificador.decidir(self.con, AHORA)
        self.assertFalse(d["correr"])
        self.assertIn("a mano", d["motivo"])

    def test_la_primera_corrida_automatica_se_puede_pedir(self):
        with mock.patch.object(
                planificador.config, "env",
                side_effect=lambda k, d=None: "1" if k == "PULSERIVAL_CICLO_AL_ARRANCAR" else None):
            self.assertTrue(planificador.decidir(self.con, AHORA)["correr"])

    def test_no_corre_antes_de_cumplir_la_cadencia(self):
        # Este es EL caso que protege la plata: el hilo despierta cada hora y
        # casi siempre la respuesta tiene que ser que no.
        self.corrida(hace_dias=2)
        d = planificador.decidir(self.con, AHORA)
        self.assertFalse(d["correr"])
        self.assertIn("2 días", d["motivo"])
        self.assertGreater(d["proxima_en_horas"], 0)

    def test_corre_cuando_pasó_la_cadencia(self):
        self.corrida(hace_dias=7)
        self.assertTrue(planificador.decidir(self.con, AHORA)["correr"])

    def test_una_corrida_atrasada_se_recupera(self):
        # La razón de que esto no sea un cron: si la máquina estaba caída el
        # lunes a las 5, un cron pierde esa semana para siempre. Acá la
        # siguiente vez que despierta ve el atraso y corre.
        self.corrida(hace_dias=11)
        d = planificador.decidir(self.con, AHORA)
        self.assertTrue(d["correr"])
        self.assertIn("11 días", d["motivo"])

    def test_espera_la_hora_preferida_el_dia_que_le_toca(self):
        # Cumple la cadencia a las 3 de la mañana UTC; se espera a las 11 para
        # que el reporte no quede fechado a una hora cualquiera.
        temprano = AHORA.replace(hour=3)
        self.corrida(hace_dias=7, ahora=temprano)
        d = planificador.decidir(self.con, temprano)
        self.assertFalse(d["correr"])
        self.assertIn("esperando", d["motivo"])

    def test_pero_no_espera_para_siempre(self):
        # Si ya se atrasó más de un día, esperar a la hora exacta de mañana
        # sería perder otro día por prolijidad.
        temprano = AHORA.replace(hour=3)
        self.corrida(hace_dias=9, ahora=temprano)
        self.assertTrue(planificador.decidir(self.con, temprano)["correr"])

    def test_no_arranca_si_ya_hay_una_corrida_en_curso(self):
        # Protege del caso de dos procesos: el candado de memoria es de cada
        # uno, la base la ven los dos.
        self.corrida(hace_dias=10, estado="ok")
        self.corrida(hace_dias=0.01, estado="en_curso")
        d = planificador.decidir(self.con, AHORA)
        self.assertFalse(d["correr"])
        self.assertIn("en curso", d["motivo"])

    def test_una_corrida_colgada_no_bloquea_para_siempre(self):
        # Una corrida que quedó 'en_curso' porque el proceso murió no puede
        # dejar al cliente sin reportes hasta que alguien lo note a mano.
        self.corrida(hace_dias=8, estado="en_curso")
        self.assertTrue(planificador.decidir(self.con, AHORA)["correr"])

    def test_la_cadencia_es_configurable(self):
        self.corrida(hace_dias=2)
        with mock.patch.object(planificador.config, "env",
                               side_effect=lambda k, d=None: "1" if k == "PULSERIVAL_CICLO_CADA_DIAS" else None):
            self.assertTrue(planificador.decidir(self.con, AHORA)["correr"])


class TestTic(CasoPlanificador):
    """`tic()` decide contra el reloj real, así que acá las corridas se
    fechan contra el reloj real y con atraso suficiente para que la hora
    preferida no cambie el resultado."""

    def setUp(self):
        super().setUp()
        self.real = datetime.now(timezone.utc)

    def conectar(self):
        return db.conectar(self.ruta)

    def test_un_tic_que_no_toca_NO_llama_al_ciclo(self):
        # Lo más caro que puede pasar: `ciclo_completo` recolecta primero y
        # recién después mira a quién le toca, así que llamarlo cada hora
        # pagaría el scraper veinticuatro veces por día.
        self.corrida(hace_dias=1, ahora=self.real)
        p = planificador.Planificador(self.conectar)
        with mock.patch("pulserival.pipeline.ciclo_completo") as ciclo:
            resultado = p.tic()
        ciclo.assert_not_called()
        self.assertFalse(resultado["corrio"])

    def test_un_tic_que_toca_llama_al_ciclo_una_vez(self):
        self.corrida(hace_dias=9, ahora=self.real)
        p = planificador.Planificador(self.conectar)
        with mock.patch("pulserival.pipeline.ciclo_completo",
                        return_value={"reportes": [{"reporte_id": 1}]}) as ciclo:
            resultado = p.tic()
        self.assertEqual(ciclo.call_count, 1)
        self.assertTrue(resultado["corrio"])
        self.assertEqual(resultado["resultado"]["reportes"], 1)

    def test_un_ciclo_que_falla_no_mata_el_hilo(self):
        # Si una excepción se escapa, el cron deja de existir y nadie se
        # entera hasta que el cliente pregunta por qué no llegó el reporte.
        self.corrida(hace_dias=9, ahora=self.real)
        p = planificador.Planificador(self.conectar)
        # Se silencia el registro solo acá: que el fallo se registre es
        # correcto, pero el traceback ensucia la salida de los tests.
        with mock.patch("pulserival.pipeline.ciclo_completo",
                        side_effect=RuntimeError("apify se cayó")), \
             mock.patch.object(planificador.registro, "exception"):
            resultado = p.tic()
        self.assertIn("apify se cayó", resultado["resultado"]["error"])

    def test_el_modo_y_el_limite_salen_del_entorno(self):
        self.corrida(hace_dias=9, ahora=self.real)
        p = planificador.Planificador(self.conectar)
        valores = {"PULSERIVAL_CICLO_MODO": "demo", "PULSERIVAL_CICLO_LIMITE": "12"}
        with mock.patch.object(planificador.config, "env",
                               side_effect=lambda k, d=None: valores.get(k)), \
             mock.patch("pulserival.pipeline.ciclo_completo",
                        return_value={"reportes": []}) as ciclo:
            p.tic()
        self.assertEqual(ciclo.call_args.kwargs["modo"], "demo")
        self.assertEqual(ciclo.call_args.kwargs["limite"], 12)


class TestEncendido(CasoBase):
    def test_apagado_por_defecto(self):
        # Ni los tests ni la máquina local pueden ponerse a correr ciclos
        # solos: cuesta plata de verdad.
        import os

        os.environ.pop("PULSERIVAL_PLANIFICADOR", None)
        self.assertFalse(planificador.habilitado())
        from pulserival.web.app import crear_app

        self.assertIsNone(crear_app(str(self.ruta)).config["PLANIFICADOR"])

    def test_se_enciende_con_la_variable(self):
        import os

        os.environ["PULSERIVAL_PLANIFICADOR"] = "1"
        self.addCleanup(lambda: os.environ.pop("PULSERIVAL_PLANIFICADOR", None))
        from pulserival.web.app import crear_app

        app = crear_app(str(self.ruta))
        self.addCleanup(app.config["PLANIFICADOR"].parar)
        self.assertIsNotNone(app.config["PLANIFICADOR"])

    def test_salud_dice_que_esta_apagado(self):
        import os

        os.environ.pop("PULSERIVAL_PLANIFICADOR", None)
        self.assertIn("apagado", planificador.estado(self.con)["planificador"])


if __name__ == "__main__":
    unittest.main()
