"""La deducción de competidores: quien no indica ninguno recibe el reporte igual.

Para pedir el reporte alcanzan el nombre de la empresa y un correo. Si no hay
competidores, el ciclo los busca en la Biblioteca de Meta con los términos de
la industria y toma a los que más anuncian, menos al propio cliente.
"""
from __future__ import annotations

from unittest import mock

from pulserival import db, descubrir, pipeline
from tests.base import CasoBase


class TestTerminos(CasoBase):
    def test_salen_de_la_industria_elegida(self):
        cid = self.cliente(nombre_empresa="El Roble", industria="Ferretería y construcción")
        cliente = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertIn("ferretería", descubrir.terminos(cliente))

    def test_sin_industria_conocida_salen_del_nombre(self):
        # "Ferretería El Roble" casi siempre dice el rubro en la primera palabra.
        cid = self.cliente(nombre_empresa="Ferretería El Roble", industria="Otra")
        cliente = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(descubrir.terminos(cliente), ["Ferretería"])

    def test_las_industrias_del_formulario_son_las_del_yaml(self):
        nombres = descubrir.nombres_de_industrias()
        self.assertIn("Ferretería y construcción", nombres)
        self.assertEqual(nombres[-1], "Otra")


class TestElPropioCliente(CasoBase):
    def test_no_se_elige_a_si_mismo(self):
        self.assertTrue(descubrir.es_el_mismo("Ferretería El Roble", "El Roble S.A.",
                                              {"ferreteria"}))

    def test_compartir_el_rubro_no_lo_hace_el_mismo(self):
        self.assertFalse(descubrir.es_el_mismo("Ferretería El Roble", "Ferretería EPA",
                                               {"ferreteria"}))


class TestDeducir(CasoBase):
    def cliente_sin_competidores(self, **extra):
        datos = {"nombre_empresa": "Gimnasio Fuerza Tica", "contacto_email": "a@b.test",
                 "plan": "autoservicio", "periodicidad": "mensual",
                 "estado_suscripcion": "activa", "industria": "Gimnasios y deporte"}
        datos.update(extra)
        return self.cliente(**datos)

    def test_agrega_a_los_que_mas_anuncian_con_una_nota(self):
        cid = self.cliente_sin_competidores()
        hechos = descubrir.completar(self.con, cid, modo="demo")
        self.assertEqual(len(hechos), 1)
        comps = db.competidores_de(self.con, cid)
        self.assertGreaterEqual(len(comps), 1)
        for c in comps:
            self.assertIn("Deducido por PulseRival", c["notas"])
            # Se los puede volver a pedir: id de página en Meta y nombre en Google.
            self.assertTrue(c["meta_pagina_id"] or c["meta_consulta"])
            self.assertEqual(c["google_anunciante"], c["nombre"])

    def test_no_toca_a_quien_ya_indico_competidores(self):
        cid = self.cliente_sin_competidores()
        self.competidor(cid, nombre="EPA")
        self.assertEqual(descubrir.completar(self.con, cid, modo="demo"), [])
        self.assertEqual(len(db.competidores_de(self.con, cid)), 1)

    def test_no_gasta_en_quien_no_paga(self):
        cid = self.cliente_sin_competidores(estado_suscripcion="pendiente_pago")
        self.assertEqual(descubrir.completar(self.con, cid, modo="demo"), [])

    def test_si_la_busqueda_falla_lo_dice_y_no_inventa(self):
        from pulserival.fuentes import FuenteError

        cid = self.cliente_sin_competidores()
        falsa = mock.Mock()
        falsa.buscar.side_effect = FuenteError("Apify respondió 500")
        with mock.patch.object(descubrir, "obtener_fuente", return_value=falsa):
            hecho = descubrir.completar(self.con, cid, modo="apify")[0]
        self.assertEqual(hecho["agregados"], [])
        self.assertIn("Apify respondió 500", hecho["motivo"])
        self.assertEqual(db.competidores_de(self.con, cid), [])


class TestDePuntaAPunta(CasoBase):
    def test_un_cliente_sin_competidores_recibe_su_reporte(self):
        # Es lo que se pidió: con el nombre y el correo, el reporte sale.
        cid = self.cliente(nombre_empresa="Gimnasio Fuerza Tica", contacto_email="a@b.test",
                           plan="autoservicio", periodicidad="mensual",
                           estado_suscripcion="activa", industria="Gimnasios y deporte")
        res = pipeline.ciclo_completo(self.con, cliente_id=cid, modo="demo", exportar=False)
        self.assertTrue(res["deducidos"][0]["agregados"])
        hechos = [r for r in res["reportes"] if r.get("reporte_id")]
        self.assertEqual(len(hechos), 1, res["reportes"])


class TestBusquedaEnApify(CasoBase):
    def test_busca_por_palabra_en_costa_rica_y_no_filtra_por_anunciante(self):
        from pulserival.fuentes import apify

        with mock.patch.dict("os.environ", {"APIFY_TOKEN": "x"}):
            fuente = apify.FuenteApify("meta")
        entrada = fuente.construir_entrada({"nombre": "gimnasio", "meta_consulta": "gimnasio"}, 40)
        url = entrada["startUrls"][0]["url"]
        self.assertIn("country=CR", url)
        self.assertIn("q=gimnasio", url)
        with mock.patch.object(fuente, "_correr_actor", return_value=[
                {"adArchiveID": "1", "pageName": "Otro Gym", "pageID": "77",
                 "snapshot": {"body": {"text": "Matrícula gratis"}}}]), \
             mock.patch.object(fuente, "_guardar_crudo"):
            anuncios = fuente.buscar("gimnasio")
        self.assertEqual(anuncios[0].anunciante, "Otro Gym")
        self.assertEqual(anuncios[0].metadata["anunciante_id"], "77")
