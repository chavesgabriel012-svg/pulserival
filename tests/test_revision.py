"""Registro de ediciones: el dataset que habilita las Fases 2 y 3."""
from __future__ import annotations

import json

from pulserival import db, util
from pulserival.revision import flujo
from tests.base import CasoBase

BORRADOR = """## Lo más importante de esta semana

Vital Gym bajó el precio [A1].

## Qué haría yo esta semana

Revisar la oferta.
"""


class TestRevision(CasoBase):
    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        inicio, fin = util.periodo("semanal")
        self.rep = db.insertar(self.con, "reportes_generados", {
            "cliente_id": self.cli, "periodo_inicio": inicio, "periodo_fin": fin,
            "asunto": "Reporte", "borrador_md": BORRADOR, "estado": "borrador",
            "proveedor_ia": "stub", "modelo_ia": "stub", "version_prompt": "v1",
            "datos_json": json.dumps({"anuncios": [], "conteo": {}}),
        })
        self.con.commit()

    def test_exportar_e_importar_sin_cambios(self):
        ruta = flujo.exportar(self.con, self.rep)
        self.assertTrue(ruta.exists())
        self.assertIn("no se envía al cliente", ruta.read_text(encoding="utf-8"))
        cuerpo = flujo.leer_cuerpo(ruta)
        self.assertEqual(cuerpo.strip(), BORRADOR.strip(),
                         "el encabezado de instrucciones no debe contaminar el reporte")
        res = flujo.registrar_final(self.con, self.rep, ruta=ruta, autoetiquetar=False)
        self.assertFalse(res["cambios"])
        self.assertEqual(res["etiqueta"], "sin_cambios")

    def test_registra_diff_y_etiqueta(self):
        final = BORRADOR.replace("Revisar la oferta.", "Bajar la matrícula esta semana.")
        res = flujo.registrar_final(self.con, self.rep, final_md=final,
                                    etiqueta="recomendacion_debil",
                                    razon="La recomendación era genérica", autoetiquetar=False)
        self.assertTrue(res["cambios"])
        self.assertLess(res["similitud"], 1.0)
        fila = db.fila(self.con, "SELECT * FROM ediciones_registradas WHERE id = ?", (res["edicion_id"],))
        self.assertEqual(fila["etiqueta"], "recomendacion_debil")
        self.assertIn("Bajar la matrícula", fila["diff_unificado"])
        bloques = db.leer_json(fila["bloques_json"], [])
        editadas = [b for b in bloques if b["estado"] == "editada"]
        self.assertEqual([b["seccion"] for b in editadas], ["Qué haría yo esta semana"],
                         "el diff por sección dice qué parte reescribo siempre")

    def test_reporte_queda_en_revisado(self):
        flujo.registrar_final(self.con, self.rep, final_md=BORRADOR + "extra", autoetiquetar=False)
        estado = db.fila(self.con, "SELECT estado, final_md FROM reportes_generados WHERE id = ?", (self.rep,))
        self.assertEqual(estado["estado"], "revisado")
        self.assertIn("extra", estado["final_md"])

    def test_dataset_y_metricas(self):
        flujo.registrar_final(self.con, self.rep, final_md=BORRADOR + "x",
                              etiqueta="tono", autoetiquetar=False)
        destino = flujo.exportar_dataset(self.con)
        lineas = destino.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lineas), 1)
        registro = json.loads(lineas[0])
        for campo in ("borrador", "final", "diff", "etiqueta", "entrada", "generador"):
            self.assertIn(campo, registro)
        m = flujo.metricas(self.con)
        self.assertEqual(m["reportes_revisados"], 1)
        self.assertFalse(m["listo_para_fase_3"], "con un solo reporte no se automatiza")
