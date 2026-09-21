"""El pipeline completo y las reglas de seguridad del envío."""
from __future__ import annotations

import os

from pulserival import db, pipeline, util
from pulserival.entrega import EnvioError, enviar_reporte
from pulserival.reporte import generar as generar_mod
from tests.base import CasoBase


class TestPipeline(CasoBase):
    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.competidor(self.cli, "Vital Gym CR")
        self.competidor(self.cli, "Club Atlas Escazú", google_dominio=None)

    def test_ciclo_completo_en_modo_demo(self):
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        res = pipeline.ciclo_completo(self.con, modo="demo")
        self.assertGreater(res["totales"]["nuevos"], 0)
        self.assertEqual(res["errores"], [])
        self.assertEqual(len(res["reportes"]), 1)
        rep = res["reportes"][0]
        self.assertIn("reporte_id", rep)
        self.assertTrue(rep["validacion"]["aprobado"], rep["validacion"]["problemas"])
        self.assertTrue(os.path.exists(rep["archivo"]))

    def test_segunda_corrida_detecta_cambios(self):
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        pipeline.recolectar(self.con, modo="demo")
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "2"
        corrida = pipeline.recolectar(self.con, modo="demo")
        t = corrida.totales()
        self.assertEqual(t["cambiados"], 1, "el cambio de precio debe detectarse")
        self.assertEqual(t["pausados"], 1, "el anuncio que se cayó debe detectarse")
        self.assertGreater(t["nuevos"], 0)

    def test_corrida_registrada_para_auditoria(self):
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        corrida = pipeline.recolectar(self.con, modo="demo")
        fila = db.fila(self.con, "SELECT * FROM corridas_recoleccion WHERE id = ?", (corrida.id,))
        self.assertEqual(fila["estado"], "ok")
        resumen = db.leer_json(fila["resumen_json"], {})
        self.assertIn("totales", resumen)
        self.assertIn("por_competidor", resumen)

    def test_competidor_sin_datos_de_google_se_salta_sin_error(self):
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        corrida = pipeline.recolectar(self.con, modo="demo")
        self.assertTrue(any(s["plataforma"] == "google" and "Atlas" in s["competidor"]
                            for s in corrida.saltados))
        self.assertEqual(corrida.errores, [])

    def test_no_regenera_reporte_ya_existente(self):
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        pipeline.recolectar(self.con, modo="demo")
        cliente = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (self.cli,))
        inicio, fin = util.periodo("semanal")
        r1 = generar_mod.generar(self.con, cliente, inicio, fin)
        r2 = generar_mod.generar(self.con, cliente, inicio, fin)
        self.assertEqual(r1["reporte_id"], r2["reporte_id"])
        self.assertTrue(r2["ya_existia"])

    def test_periodo_sin_movimiento_genera_reporte_honesto(self):
        cliente = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (self.cli,))
        inicio, fin = util.periodo("semanal")
        rep = generar_mod.generar(self.con, cliente, inicio, fin)
        self.assertIn("no detectamos actividad", rep["borrador_md"])
        self.assertTrue(rep["validacion"]["aprobado"])


class TestSegurosDeEnvio(CasoBase):
    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.competidor(self.cli)
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        pipeline.recolectar(self.con, modo="demo")
        cliente = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (self.cli,))
        inicio, fin = util.periodo("semanal")
        self.rep = generar_mod.generar(self.con, cliente, inicio, fin)["reporte_id"]
        self.con.commit()

    def test_no_envia_un_borrador_sin_revisar(self):
        with self.assertRaises(EnvioError) as ctx:
            enviar_reporte(self.con, self.rep)
        self.assertIn("borrador", str(ctx.exception))

    def test_simular_no_envia_pero_deja_los_archivos(self):
        res = enviar_reporte(self.con, self.rep, simular=True)
        self.assertFalse(res["enviado"])
        self.assertTrue(os.path.exists(res["archivo"]))
        estado = db.fila(self.con, "SELECT estado FROM reportes_generados WHERE id = ?", (self.rep,))["estado"]
        self.assertEqual(estado, "borrador", "simular no debe marcar el reporte como enviado")

    def test_sin_configuracion_de_correo_avisa_claro(self):
        db.actualizar(self.con, "reportes_generados", self.rep, {"estado": "revisado"})
        with self.assertRaises(EnvioError) as ctx:
            enviar_reporte(self.con, self.rep)
        self.assertIn("No hay forma de enviar configurada", str(ctx.exception))


class TestTopeDeLlamadasDeIA(CasoBase):
    """El enriquecimiento por anuncio no puede quemar la cuota del reporte.

    Con cuatro competidores grandes salieron 99 anuncios nuevos. Una llamada
    por anuncio agota el límite por minuto de los tiers gratuitos, y entonces
    falla también la redacción final, que es la que el cliente lee. El
    enriquecimiento es una ayuda; el reporte es el producto.
    """

    def anuncios(self, n, prioridad=1):
        return [{"anuncio_id": i, "clasificacion": "nuevo", "competidor": "X",
                 "plataforma": "meta", "titulo": f"Anuncio {i}", "texto": "promo",
                 "prioridad": prioridad, "variantes": 1, "fecha_inicio": "2026-09-01",
                 "tipo_creativo": "imagen", "cta": None, "link_destino": None,
                 "descripcion": None, "analisis": None} for i in range(1, n + 1)]

    def test_se_respeta_el_tope_configurado(self):
        procesados = generar_mod.enriquecer_anuncios(self.con, self.anuncios(50), maximo=10)
        self.assertEqual(procesados, 10)

    def test_los_de_prioridad_1_van_primero(self):
        anuncios = self.anuncios(3, prioridad=2) + self.anuncios(3, prioridad=1)
        for i, a in enumerate(anuncios):
            a["anuncio_id"] = i + 100
        generar_mod.enriquecer_anuncios(self.con, anuncios, maximo=3)
        analizados = [a for a in anuncios if a.get("analisis")]
        self.assertTrue(all(a["prioridad"] == 1 for a in analizados))

    def test_si_la_ia_falla_seguido_se_corta(self):
        from unittest import mock

        from pulserival.ia.base import ProveedorError

        with mock.patch("pulserival.reporte.generar.ejecutar",
                        side_effect=ProveedorError("429")) as llamada:
            generar_mod.enriquecer_anuncios(self.con, self.anuncios(25), maximo=25)
        self.assertLessEqual(llamada.call_count, 4,
                             "tras varios fallos seguidos hay que dejar de insistir")

    def test_un_anuncio_ya_analizado_no_se_vuelve_a_pagar(self):
        anuncios = self.anuncios(3)
        anuncios[0]["analisis"] = {"angulo": "ya estaba"}
        procesados = generar_mod.enriquecer_anuncios(self.con, anuncios, maximo=10)
        self.assertEqual(procesados, 2)


class TestTamanoDelPrompt(CasoBase):
    """El prompt no puede crecer sin límite con la cantidad de anuncios.

    Real: con 99 anuncios, un modelo respondió 413 "Request too large" y el
    reporte cayó al modo sin IA. El detalle del correo sigue listando todos;
    lo que se acota es cuántos ve el modelo para escribir el análisis.
    """

    def anuncios(self, n):
        clases = ("continua", "nuevo", "cambiado", "pausado")
        return [{"anuncio_id": i, "clasificacion": clases[i % 4], "competidor": "X",
                 "plataforma": "meta", "prioridad": 1 + (i % 2), "variantes": 1 + (i % 3),
                 "sin_texto": i % 5 == 0, "referencia": f"[A{i}]"} for i in range(1, n + 1)]

    def test_se_acota_la_cantidad_que_ve_el_modelo(self):
        from pulserival import config

        seleccion = generar_mod._seleccionar_para_prompt(self.anuncios(200))
        self.assertEqual(len(seleccion), config.max_anuncios_en_prompt())

    def test_se_prioriza_lo_que_es_noticia(self):
        seleccion = generar_mod._seleccionar_para_prompt(self.anuncios(200))
        clases = [a["clasificacion"] for a in seleccion]
        self.assertNotIn("continua", clases[:10],
                         "lo nuevo y lo que cambió va antes que lo que sigue igual")

    def test_con_pocos_anuncios_entran_todos(self):
        seleccion = generar_mod._seleccionar_para_prompt(self.anuncios(5))
        self.assertEqual(len(seleccion), 5)

    def test_el_analisis_hecho_por_reglas_se_rehace_cuando_hay_ia(self):
        """El respaldo sin IA marca su salida como generada por reglas. Si en
        la siguiente corrida la IA sí responde, ese análisis pobre tiene que
        reemplazarse, no darse por bueno."""
        anuncios = [{"anuncio_id": 1, "clasificacion": "nuevo", "competidor": "X",
                     "plataforma": "meta", "titulo": "Promo", "texto": "2x1",
                     "prioridad": 1, "variantes": 1, "tipo_creativo": "imagen",
                     "cta": None, "link_destino": None, "descripcion": None,
                     "fecha_inicio": None,
                     "analisis": {"angulo": "x", "generado_por": "reglas"}}]
        self.assertEqual(generar_mod.enriquecer_anuncios(self.con, anuncios, maximo=5), 1)
