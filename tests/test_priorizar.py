"""El corazón del producto: detectar qué cambió de una corrida a otra."""
from __future__ import annotations

from pulserival import db, priorizar
from pulserival.fuentes.base import AnuncioCrudo
from tests.base import CasoBase


def anuncio(**kw) -> AnuncioCrudo:
    base = dict(plataforma="meta", fuente="test", id_externo="X1",
                titulo="Matrícula gratis", texto="Plan mensual ¢19.900",
                link_destino="https://ejemplo.test/promo?utm_source=fb")
    base.update(kw)
    return AnuncioCrudo(**base)


class TestConciliar(CasoBase):
    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.comp_id = self.competidor(self.cli)
        self.comp = db.fila(self.con, "SELECT * FROM competidores_seguidos WHERE id = ?", (self.comp_id,))

    def conciliar(self, anuncios):
        r = priorizar.conciliar(self.con, self.comp, "meta", anuncios)
        self.con.commit()
        return r

    def test_primera_corrida_todo_nuevo(self):
        r = self.conciliar([anuncio(), anuncio(id_externo="X2", titulo="Otro")])
        self.assertEqual(r.resumen(), {"nuevos": 2, "cambiados": 0, "continuan": 0, "pausados": 0})

    def test_segunda_corrida_igual_no_duplica(self):
        self.conciliar([anuncio()])
        r = self.conciliar([anuncio()])
        self.assertEqual(r.resumen()["continuan"], 1)
        self.assertEqual(r.resumen()["nuevos"], 0)
        total = db.fila(self.con, "SELECT COUNT(*) AS n FROM anuncios_detectados")["n"]
        self.assertEqual(total, 1, "no debe guardar el mismo anuncio dos veces")

    def test_cambio_de_precio_es_cambiado_no_nuevo(self):
        self.conciliar([anuncio()])
        r = self.conciliar([anuncio(texto="Plan mensual ¢16.900")])
        self.assertEqual(r.resumen()["cambiados"], 1)
        self.assertEqual(r.resumen()["nuevos"], 0)
        self.assertEqual(r.resumen()["pausados"], 0,
                         "la versión anterior no cuenta como anuncio pausado")

    def test_desaparecer_es_pausado(self):
        self.conciliar([anuncio(), anuncio(id_externo="X2", titulo="Otro")])
        r = self.conciliar([anuncio()])
        self.assertEqual(r.resumen()["pausados"], 1)
        estado = db.fila(self.con, "SELECT estado FROM anuncios_detectados WHERE id_externo='X2'")["estado"]
        self.assertEqual(estado, "pausado")

    def test_utm_distinto_no_es_anuncio_nuevo(self):
        self.conciliar([anuncio()])
        r = self.conciliar([anuncio(link_destino="https://ejemplo.test/promo?utm_source=ig&x=1")])
        self.assertEqual(r.resumen()["continuan"], 1,
                         "cambiar parámetros de campaña del link no es un anuncio nuevo")

    def test_mayusculas_no_son_anuncio_nuevo(self):
        self.conciliar([anuncio()])
        r = self.conciliar([anuncio(titulo="MATRÍCULA GRATIS")])
        self.assertEqual(r.resumen()["continuan"], 1)

    def test_anuncio_sin_id_externo_se_maneja(self):
        self.conciliar([anuncio(id_externo=None)])
        r = self.conciliar([anuncio(id_externo=None, texto="otro texto")])
        self.assertEqual(r.resumen()["nuevos"], 1)

    def test_scraper_que_devuelve_duplicados_no_rompe_la_corrida(self):
        r = self.conciliar([anuncio(), anuncio(), anuncio(id_externo="X2", titulo="Otro")])
        self.assertEqual(r.resumen()["nuevos"], 2, "el duplicado se ignora, no duplica ni falla")
        total = db.fila(self.con, "SELECT COUNT(*) AS n FROM anuncios_detectados")["n"]
        self.assertEqual(total, 2)

    def test_dos_anuncios_distintos_con_el_mismo_texto(self):
        # Mismo contenido, distinto id de plataforma: la huella colisiona.
        r = self.conciliar([anuncio(id_externo="X1"), anuncio(id_externo="X2")])
        self.assertEqual(r.resumen()["nuevos"], 1, "no se guarda dos veces el mismo contenido")


class TestNuncaTuvoDatos(CasoBase):
    """Cero anuncios sin historial no es lo mismo que "no pauta".

    Artelec tenía ~51 anuncios activos en Meta mientras el sistema la
    reportaba como ausente, y el reporte llegó a recomendar aprovechar ese
    hueco. La corrida tiene que señalarlo para que se revise la config.
    """

    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.comp_id = self.competidor(self.cli, "Artelec")
        self.comp = db.fila(self.con,
                            "SELECT * FROM competidores_seguidos WHERE id = ?",
                            (self.comp_id,))

    def test_sin_anuncios_y_sin_historial_se_marca(self):
        r = priorizar.conciliar(self.con, self.comp, "meta", [])
        self.assertTrue(r.nunca_tuvo_datos)
        self.assertFalse(r.sospechosa, "no es una corrida sospechosa: nunca hubo datos")

    def test_con_historial_previo_es_sospechosa_no_nunca(self):
        priorizar.conciliar(self.con, self.comp, "meta", [anuncio()])
        self.con.commit()
        r = priorizar.conciliar(self.con, self.comp, "meta", [])
        self.assertTrue(r.sospechosa)
        self.assertFalse(r.nunca_tuvo_datos)

    def test_con_anuncios_no_se_marca_nada(self):
        r = priorizar.conciliar(self.con, self.comp, "meta", [anuncio()])
        self.assertFalse(r.nunca_tuvo_datos)
        self.assertFalse(r.sospechosa)
