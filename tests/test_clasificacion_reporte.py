"""Clasificación de los anuncios que entran al reporte del periodo."""
from __future__ import annotations

from pulserival import db, util
from pulserival.reporte import datos as datos_mod
from tests.base import CasoBase


class TestClasificar(CasoBase):
    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.comp = self.competidor(self.cli)
        self.inicio, self.fin = util.periodo("semanal")

    def guardar(self, **kw):
        base = dict(competidor_id=self.comp, plataforma="meta", fuente="test",
                    huella=util.huella(kw.get("titulo", "t"), kw.get("id_externo", "")),
                    id_externo="X1", titulo="Título", texto="Texto",
                    visto_primero_en=self.fin, visto_ultimo_en=self.fin, estado="activo")
        base.update(kw)
        i = db.insertar(self.con, "anuncios_detectados", base)
        self.con.commit()
        return i

    def test_nuevo_en_periodo(self):
        self.guardar()
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual([a["clasificacion"] for a in res], ["nuevo"])
        self.assertEqual(res[0]["referencia"], "[A1]")

    def test_version_vieja_no_se_reporta_dos_veces(self):
        self.guardar(titulo="v1", visto_primero_en="2026-01-01", estado="pausado",
                     visto_ultimo_en=self.fin, huella="h1")
        self.guardar(titulo="v2", huella="h2")
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual(len(res), 1, "solo la versión nueva cuenta la historia")
        self.assertEqual(res[0]["clasificacion"], "cambiado")

    def test_pausado_en_periodo(self):
        self.guardar(visto_primero_en="2026-01-01", estado="pausado", visto_ultimo_en=self.fin)
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual([a["clasificacion"] for a in res], ["pausado"])

    def test_sigue_corriendo(self):
        self.guardar(visto_primero_en="2026-01-01", visto_ultimo_en=self.fin)
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual([a["clasificacion"] for a in res], ["continua"])

    def test_viejo_fuera_del_periodo_se_ignora(self):
        self.guardar(visto_primero_en="2026-01-01", visto_ultimo_en="2026-01-02", estado="pausado")
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual(res, [])

    def test_referencias_unicas_y_ordenadas(self):
        for i in range(3):
            self.guardar(id_externo=f"X{i}", huella=f"h{i}")
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual([a["referencia"] for a in res], ["[A1]", "[A2]", "[A3]"])
        self.assertEqual(datos_mod.conteo(res)["nuevo"], 3)


class TestVariantesYPlantillas(CasoBase):
    """Dos problemas que aparecieron con datos reales de Gollo."""

    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.comp = self.competidor(self.cli)
        self.inicio, self.fin = util.periodo("semanal")

    def guardar(self, **kw):
        base = dict(competidor_id=self.comp, plataforma="meta", fuente="test",
                    huella=util.huella(kw.get("titulo"), kw.get("texto"), kw.get("id_externo")),
                    id_externo="X1", titulo=None, texto="Texto",
                    visto_primero_en=self.fin, visto_ultimo_en=self.fin, estado="activo")
        base.update(kw)
        i = db.insertar(self.con, "anuncios_detectados", base)
        self.con.commit()
        return i

    def test_el_mismo_anuncio_en_varias_piezas_se_cuenta_una_vez(self):
        # Real: el mismo aviso de una óptica apareció 3 veces, una por sede.
        for n in range(3):
            self.guardar(id_externo=f"X{n}", texto="¿Dolores de cabeza? Revisá tu vista hoy",
                         huella=f"h{n}")
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual(len(res), 1, "sin agrupar, el reporte repite el mismo mensaje 3 veces")
        self.assertEqual(res[0]["variantes"], 3)
        self.assertEqual(len(res[0]["variantes_ids"]), 3)

    def test_anuncios_distintos_no_se_agrupan(self):
        self.guardar(id_externo="X1", texto="Promo de matrícula", huella="h1")
        self.guardar(id_externo="X2", texto="Clases de natación", huella="h2")
        self.assertEqual(len(datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)), 2)

    def test_el_catalogo_dinamico_se_marca_sin_texto(self):
        # Real: los anuncios de catálogo de Meta devuelven "{{product.brand}}".
        self.guardar(id_externo="X9", texto="{{product.brand}}", huella="hd")
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertTrue(res[0]["sin_texto"])
        self.assertTrue(res[0]["es_catalogo_dinamico"])
        self.assertIsNone(res[0]["texto"], "no se le muestra una plantilla al cliente")

    def test_un_anuncio_sin_texto_no_se_agrupa_con_otro(self):
        # Sin texto no hay forma de saber si dos piezas dicen lo mismo.
        self.guardar(id_externo="G1", texto=None, titulo=None, huella="g1", plataforma="google")
        self.guardar(id_externo="G2", texto=None, titulo=None, huella="g2", plataforma="google")
        res = datos_mod.clasificar(self.con, self.cli, self.inicio, self.fin)
        self.assertEqual(len(res), 2)
        self.assertTrue(all(a["sin_texto"] for a in res))
