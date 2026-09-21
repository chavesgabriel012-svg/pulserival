"""Proyección de costos: que los números que decidan tu plan sean correctos."""
from __future__ import annotations

from pulserival import presupuesto
from tests.base import CasoBase


class TestPresupuesto(CasoBase):
    def test_sin_clientes_no_proyecta_gasto(self):
        p = presupuesto.proyectar(self.con)
        self.assertEqual(p["scrapers_usd_mes"], 0.0)
        self.assertTrue(p["alcanza_el_credito"])

    def test_un_cliente_semanal_de_dos_plataformas(self):
        cli = self.cliente(periodicidad="semanal")
        self.competidor(cli, "A")          # tiene meta y google
        p = presupuesto.proyectar(self.con, limite_por_competidor=40)
        # 40 anuncios x 4,33 corridas = ~173 por plataforma.
        # Meta en plan free: 0,173 x $5,80 = $1,00 · Google: 0,173 x $1,20 = $0,21
        self.assertAlmostEqual(p["scrapers_usd_mes"], 1.21, places=2)
        self.assertTrue(p["alcanza_el_credito"], "un cliente tiene que entrar en el plan gratuito")

    def test_competidor_de_una_sola_plataforma_cuesta_menos(self):
        cli = self.cliente()
        self.competidor(cli, "Solo Meta", google_dominio=None)
        solo_meta = presupuesto.proyectar(self.con)["scrapers_usd_mes"]
        self.competidor(cli, "Ambas")
        ambas = presupuesto.proyectar(self.con)["scrapers_usd_mes"]
        self.assertGreater(ambas, solo_meta)

    def test_mensual_cuesta_menos_que_semanal(self):
        cli_s = self.cliente(periodicidad="semanal")
        self.competidor(cli_s, "A")
        semanal = presupuesto.proyectar(self.con)["scrapers_usd_mes"]
        self.con.execute("UPDATE clientes SET periodicidad = 'mensual' WHERE id = ?", (cli_s,))
        mensual = presupuesto.proyectar(self.con)["scrapers_usd_mes"]
        self.assertLess(mensual, semanal)
        self.assertAlmostEqual(mensual, semanal / 4.33, places=2)

    def test_avisa_cuando_no_alcanza_el_credito(self):
        for i in range(8):
            cli = self.cliente(nombre_empresa=f"Cliente {i}")
            for j in range(3):
                self.competidor(cli, f"Comp {i}-{j}")
        p = presupuesto.proyectar(self.con)
        self.assertFalse(p["alcanza_el_credito"])
        self.assertIn("subí de plan", presupuesto.formatear(p))

    def test_el_texto_es_legible(self):
        cli = self.cliente()
        self.competidor(cli, "A")
        texto = presupuesto.formatear(presupuesto.proyectar(self.con))
        self.assertIn("Plan de Apify", texto)
        self.assertIn("/mes", texto)
