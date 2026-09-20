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
