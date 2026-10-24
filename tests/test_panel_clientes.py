"""Clientes y competidores desde el panel.

El reporte sale aunque el cliente no indique competidores, así que el panel
tiene que dejar resolver lo que eso deja abierto: clientes sin competidores,
competidores que eligió el sistema y competidores que nunca trajeron anuncios.
"""
from __future__ import annotations

import os
import time
from unittest import mock

from pulserival import db, descubrir
from tests.test_panel import CasoPanel


class CasoClientes(CasoPanel):
    def setUp(self):
        super().setUp()
        # El ciclo del panel usa el modo del servidor: en los tests, la demo.
        previo = os.environ.get("PULSERIVAL_CICLO_MODO")
        os.environ["PULSERIVAL_CICLO_MODO"] = "demo"
        self.addCleanup(lambda: os.environ.pop("PULSERIVAL_CICLO_MODO", None)
                        if previo is None else os.environ.update(PULSERIVAL_CICLO_MODO=previo))
        self.nuevo = self.cliente(clave="roble", nombre_empresa="Ferretería El Roble",
                                  contacto_email="ana@roble.test", plan="autoservicio",
                                  periodicidad="mensual", estado_suscripcion="activa",
                                  industria="Ferretería y construcción")
        self.entrar()

    def post(self, ruta: str, **datos):
        return self.web.post(ruta, data={"csrf": self.token(), **datos})

    def esperar_tarea(self, cid: int) -> dict:
        tareas = self.app.config["TAREAS"]
        for _ in range(200):
            estado = tareas.estado(cid)
            if estado and estado["estado"] != "corriendo":
                return estado
            time.sleep(0.05)
        self.fail("la tarea no terminó")


class TestQueNecesitaAtencion(CasoClientes):
    def test_la_bandeja_avisa_del_cliente_sin_competidores(self):
        h = self.web.get("/panel/").get_data(as_text=True)
        self.assertIn("necesita", h)
        self.assertIn("Ferretería El Roble", h)
        self.assertIn(f"/panel/cliente/{self.nuevo}", h)

    def test_la_lista_pone_arriba_lo_que_hay_que_resolver(self):
        ok = self.cliente(clave="ok", nombre_empresa="Todo Bien S.A.", estado_suscripcion="activa")
        self.competidor(ok, nombre="Alguien")
        h = self.web.get("/panel/clientes").get_data(as_text=True)
        self.assertLess(h.index("Ferretería El Roble"), h.index("Todo Bien S.A."))
        self.assertIn("Ninguno", h)

    def test_un_competidor_elegido_por_el_sistema_se_marca_hasta_confirmarlo(self):
        # Como lo deja descubrir.deducir: id de página y nombre, sin dominio.
        comp = self.competidor(self.nuevo, nombre="EPA", meta_pagina_id="123",
                               meta_consulta=None, google_dominio=None, google_anunciante="EPA",
                               notas=descubrir.NOTA.format(termino="ferretería"))
        h = self.web.get(f"/panel/cliente/{self.nuevo}").get_data(as_text=True)
        self.assertIn("Elegido por el sistema", h)
        # Confirmarlo es guardarlo sin cambios: conserva el id con que se lo
        # encontró y deja de pedir revisión.
        self.post(f"/panel/competidor/{comp}/editar", nombre="EPA", facebook="", web="",
                  prioridad="1")
        fila = db.fila(self.con, "SELECT * FROM competidores_seguidos WHERE id = ?", (comp,))
        self.assertEqual(fila["meta_pagina_id"], "123")
        self.assertIn("confirmado", fila["notas"])
        h = self.web.get(f"/panel/cliente/{self.nuevo}").get_data(as_text=True)
        self.assertNotIn('<span class="marca lleno">Elegido por el sistema</span>', h)

    def test_un_competidor_sin_anuncios_despues_de_una_corrida_se_marca(self):
        self.competidor(self.nuevo, nombre="Nadie Pauta")
        db.insertar(self.con, "corridas_recoleccion", {
            "fuente": "demo", "estado": "ok", "disparada_por": "cron",
            "terminada_en": "2999-01-01 00:00:00"})
        self.con.commit()
        h = self.web.get(f"/panel/cliente/{self.nuevo}").get_data(as_text=True)
        self.assertIn("Sin anuncios", h)

    def test_el_reporte_avisa_si_sus_competidores_piden_revision(self):
        h = self.web.get(f"/panel/reporte/{self.rid}").get_data(as_text=True)
        self.assertIn("Revisar los competidores de este cliente", h)


class TestEditarCompetidores(CasoClientes):
    def test_se_agrega_con_solo_el_nombre(self):
        r = self.post(f"/panel/cliente/{self.nuevo}/competidores", nombre="Ferretería EPA")
        self.assertEqual(r.status_code, 302)
        comp = db.competidores_de(self.con, self.nuevo)[0]
        self.assertEqual(comp["meta_consulta"], "Ferretería EPA")
        self.assertEqual(comp["google_anunciante"], "Ferretería EPA")

    def test_no_se_agrega_dos_veces(self):
        self.post(f"/panel/cliente/{self.nuevo}/competidores", nombre="EPA")
        r = self.post(f"/panel/cliente/{self.nuevo}/competidores", nombre="epa")
        self.assertEqual(r.status_code, 409)

    def test_quitar_lo_desactiva_y_volver_a_agregarlo_lo_reactiva(self):
        self.post(f"/panel/cliente/{self.nuevo}/competidores", nombre="EPA")
        comp = db.competidores_de(self.con, self.nuevo)[0]["id"]
        self.post(f"/panel/competidor/{comp}/quitar")
        self.assertEqual(db.competidores_de(self.con, self.nuevo), [])
        self.post(f"/panel/cliente/{self.nuevo}/competidores", nombre="EPA", web="epa.cr")
        comps = db.competidores_de(self.con, self.nuevo)
        # El mismo registro, con sus anuncios de antes: no uno nuevo.
        self.assertEqual([c["id"] for c in comps], [comp])
        self.assertEqual(comps[0]["google_dominio"], "epa.cr")

    def test_cambiar_como_se_lo_busca_borra_el_id_viejo(self):
        comp = self.competidor(self.nuevo, nombre="EPA", meta_pagina_id="123")
        self.post(f"/panel/competidor/{comp}/editar", nombre="EPA",
                  facebook="facebook.com/EPACostaRica", web="")
        fila = db.fila(self.con, "SELECT * FROM competidores_seguidos WHERE id = ?", (comp,))
        self.assertIsNone(fila["meta_pagina_id"])
        self.assertEqual(fila["meta_pagina_url"], "https://facebook.com/EPACostaRica")

    def test_los_datos_del_cliente_se_corrigen(self):
        self.post(f"/panel/cliente/{self.nuevo}/datos", email="nuevo@roble.test",
                  industria="Otra", notas="Dos sedes.")
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (self.nuevo,))
        self.assertEqual(fila["contacto_email"], "nuevo@roble.test")
        self.assertEqual(fila["industria"], "Otra")
        self.assertEqual(fila["notas"], "Dos sedes.")

    def test_un_correo_invalido_no_se_guarda(self):
        r = self.post(f"/panel/cliente/{self.nuevo}/datos", email="no-es-correo")
        self.assertEqual(r.status_code, 400)

    def test_el_nombre_de_un_competidor_no_se_ejecuta_en_la_pagina(self):
        # El nombre puede venir del formulario público. Antes iba adentro de
        # un onsubmit="confirm('...')": una comilla bastaba para meter código.
        self.competidor(self.nuevo, nombre="X'); alert(1); ('")
        h = self.web.get(f"/panel/cliente/{self.nuevo}").get_data(as_text=True)
        self.assertNotIn("onsubmit", h)
        self.assertNotIn("alert(1); ('\"", h)
        self.assertIn("data-confirmar=", h)


class TestAccionesQueGastan(CasoClientes):
    def test_sin_la_casilla_no_se_hace_nada(self):
        for accion in ("buscar", "generar", "rehacer"):
            with self.subTest(accion=accion):
                r = self.post(f"/panel/cliente/{self.nuevo}/{accion}")
                self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.app.config["TAREAS"].estado(self.nuevo))

    def test_buscar_agrega_competidores_sin_repetir(self):
        self.post(f"/panel/cliente/{self.nuevo}/competidores", nombre="Vital Gym CR")
        r = self.post(f"/panel/cliente/{self.nuevo}/buscar", confirmo="si")
        self.assertEqual(r.status_code, 302)
        estado = self.esperar_tarea(self.nuevo)
        self.assertEqual(estado["estado"], "terminada", estado)
        nombres = [c["nombre"] for c in db.competidores_de(self.con, self.nuevo)]
        self.assertEqual(nombres.count("Vital Gym CR"), 1)
        self.assertIn("Club Atlas Escazú", nombres)

    def test_generar_le_hace_el_reporte_a_quien_no_tenia_competidores(self):
        self.post(f"/panel/cliente/{self.nuevo}/generar", confirmo="si")
        estado = self.esperar_tarea(self.nuevo)
        self.assertEqual(estado["estado"], "terminada", estado)
        self.assertTrue(db.competidores_de(self.con, self.nuevo))
        self.assertIsNotNone(db.fila(self.con, "SELECT id FROM reportes_generados "
                                               "WHERE cliente_id = ?", (self.nuevo,)))
        h = self.web.get(f"/panel/cliente/{self.nuevo}").get_data(as_text=True)
        self.assertIn("Reporte listo", h)

    def test_no_corre_a_la_vez_que_otra_corrida(self):
        candado = self.app.config["TAREAS"]._candado
        candado.acquire()
        try:
            r = self.post(f"/panel/cliente/{self.nuevo}/generar", confirmo="si")
        finally:
            candado.release()
        self.assertEqual(r.status_code, 409)

    def test_si_la_tarea_falla_el_panel_lo_dice(self):
        with mock.patch("pulserival.pipeline.ciclo_completo", side_effect=RuntimeError("Apify caído")):
            self.post(f"/panel/cliente/{self.nuevo}/rehacer", confirmo="si")
            estado = self.esperar_tarea(self.nuevo)
        self.assertEqual(estado["estado"], "fallo")
        h = self.web.get(f"/panel/cliente/{self.nuevo}").get_data(as_text=True)
        self.assertIn("Apify caído", h)


class TestIdentidadDeLasPantallasNuevas(CasoClientes):
    def test_usan_solo_la_paleta_de_marca(self):
        from pulserival import marca

        for ruta in ("/panel/clientes", f"/panel/cliente/{self.nuevo}"):
            with self.subTest(ruta=ruta):
                html = self.web.get(ruta).get_data(as_text=True)
                self.assertEqual(marca.fuera_de_paleta(html), [])

    def test_sin_sesion_no_se_ven(self):
        self.web.post("/panel/salir", data={"csrf": self.token()})
        for ruta in ("/panel/clientes", f"/panel/cliente/{self.nuevo}"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.web.get(ruta).status_code, 302)


class TestCSRFDeLasRutasNuevas(CasoClientes):
    def test_ningun_post_pasa_sin_token(self):
        comp = self.competidor(self.nuevo, nombre="EPA")
        for ruta in (f"/panel/cliente/{self.nuevo}/datos",
                     f"/panel/cliente/{self.nuevo}/competidores",
                     f"/panel/competidor/{comp}/editar", f"/panel/competidor/{comp}/quitar",
                     f"/panel/cliente/{self.nuevo}/buscar", f"/panel/cliente/{self.nuevo}/generar",
                     f"/panel/cliente/{self.nuevo}/rehacer"):
            with self.subTest(ruta=ruta):
                r = self.web.post(ruta, data={"nombre": "X", "confirmo": "si"})
                self.assertEqual(r.status_code, 400)
