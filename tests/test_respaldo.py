"""Respaldos de la base.

Existen porque los respaldos automáticos del volumen en Railway son del plan
Pro, y el plan de este proyecto no los tiene. Lo que hay que dejar clavado es
que la copia sea **consistente y completa**: un respaldo roto es peor que no
tener respaldo, porque se descubre el día que hace falta restaurarlo.
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from pulserival import config, db, mantenimiento
from tests.base import CasoBase


class TestRespaldar(CasoBase):
    def setUp(self):
        super().setUp()
        self.carpeta = Path(self.tmp.name) / "respaldos"
        self.cid = self.cliente(clave="gym")
        self.competidor(self.cid)

    def respaldar(self, **kw):
        return mantenimiento.respaldar(
            ruta_db=self.ruta, destino=self.carpeta, **kw)

    def test_la_copia_tiene_los_mismos_datos(self):
        r = self.respaldar()
        con = sqlite3.connect(r["archivo"])
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
        self.assertEqual(
            con.execute("SELECT nombre_empresa FROM clientes").fetchone()[0],
            "Gimnasio Fuerza Tica")

    def test_la_copia_pasa_el_chequeo_de_integridad(self):
        # Copiar el archivo con `cp` mientras alguien escribe puede dejar una
        # copia rota que además parece sana. La API de respaldo de SQLite toma
        # una instantánea coherente; esto lo comprueba.
        r = self.respaldar()
        con = sqlite3.connect(r["archivo"])
        self.addCleanup(con.close)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_funciona_con_la_base_abierta_y_escribiendo(self):
        # El caso real: el servidor está sirviendo mientras se respalda.
        db.insertar(self.con, "clientes", {
            "nombre_empresa": "Otro", "contacto_email": "o@e.test",
            "periodicidad": "semanal"})
        self.con.commit()
        r = self.respaldar()
        con = sqlite3.connect(r["archivo"])
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 2)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_la_rotacion_borra_las_mas_viejas(self):
        for n in range(5):
            (self.carpeta).mkdir(parents=True, exist_ok=True)
            (self.carpeta / f"pulserival-2026010{n}-000000.db").write_bytes(b"viejo")
        r = self.respaldar(conservar=3)
        quedan = sorted(p.name for p in self.carpeta.glob("pulserival-*.db"))
        self.assertEqual(len(quedan), 3)
        # La nueva siempre sobrevive, y las que se borran son las de fecha más vieja.
        self.assertIn(Path(r["archivo"]).name, quedan)
        self.assertNotIn("pulserival-20260100-000000.db", quedan)

    def test_conservar_cero_no_borra_nada(self):
        self.respaldar(conservar=0)
        self.respaldar(conservar=0)
        self.assertGreaterEqual(len(list(self.carpeta.glob("pulserival-*.db"))), 1)

    def test_sin_base_falla_claro(self):
        with self.assertRaises(FileNotFoundError):
            mantenimiento.respaldar(ruta_db=Path(self.tmp.name) / "no-existe.db",
                                    destino=self.carpeta)

    def test_por_defecto_va_al_directorio_de_datos(self):
        # En el servidor DIR_DATOS apunta al disco, así que el respaldo cae ahí
        # y no adentro del contenedor, que se borra en cada deploy.
        original = config.DIR_DATOS
        config.DIR_DATOS = Path(self.tmp.name) / "datos"
        self.addCleanup(lambda: setattr(config, "DIR_DATOS", original))
        r = mantenimiento.respaldar(ruta_db=self.ruta)
        self.assertTrue(r["archivo"].startswith(str(config.DIR_DATOS)))


class TestRespaldoEnElPlanificador(CasoBase):
    def test_respalda_antes_de_cada_ciclo(self):
        # Es el momento en que la base más cambia, y por lo tanto el momento en
        # que más vale poder volver atrás.
        from datetime import datetime, timedelta, timezone
        from unittest import mock

        from pulserival.web import planificador

        cuando = datetime.now(timezone.utc) - timedelta(days=9)
        db.insertar(self.con, "corridas_recoleccion", {
            "iniciada_en": cuando.strftime("%Y-%m-%d %H:%M:%S"),
            "terminada_en": cuando.strftime("%Y-%m-%d %H:%M:%S"), "estado": "ok"})
        self.con.commit()

        p = planificador.Planificador(lambda: db.conectar(self.ruta))
        with mock.patch("pulserival.mantenimiento.respaldar",
                        return_value={"archivo": "/x.db", "bytes": 1}) as respaldo, \
             mock.patch("pulserival.pipeline.ciclo_completo", return_value={"reportes": []}):
            p.tic()
        respaldo.assert_called_once()

    def test_un_respaldo_que_falla_no_frena_el_ciclo(self):
        # Dejar sin reporte al cliente por un respaldo es peor que no tenerlo.
        from datetime import datetime, timedelta, timezone
        from unittest import mock

        from pulserival.web import planificador

        cuando = datetime.now(timezone.utc) - timedelta(days=9)
        db.insertar(self.con, "corridas_recoleccion", {
            "iniciada_en": cuando.strftime("%Y-%m-%d %H:%M:%S"),
            "terminada_en": cuando.strftime("%Y-%m-%d %H:%M:%S"), "estado": "ok"})
        self.con.commit()

        p = planificador.Planificador(lambda: db.conectar(self.ruta))
        with mock.patch("pulserival.mantenimiento.respaldar",
                        side_effect=OSError("disco lleno")), \
             mock.patch("pulserival.pipeline.ciclo_completo",
                        return_value={"reportes": [{"reporte_id": 1}]}) as ciclo, \
             mock.patch.object(planificador.registro, "warning"):
            resultado = p.tic()
        ciclo.assert_called_once()
        self.assertTrue(resultado["corrio"])


if __name__ == "__main__":
    unittest.main()
