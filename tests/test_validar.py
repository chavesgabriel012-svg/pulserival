"""El control de calidad: lo que hace posible revisar rápido (y automatizar)."""
from __future__ import annotations

import unittest

from pulserival.reporte import validar

ANUNCIOS = [
    {"referencia": "[A1]", "clasificacion": "nuevo"},
    {"referencia": "[A2]", "clasificacion": "cambiado"},
    {"referencia": "[A3]", "clasificacion": "continua"},
]

BORRADOR_OK = """## Lo más importante de esta semana

Vital Gym bajó el precio de su plan mensual [A1] y cambió el texto de su
anuncio de matrícula [A2].

## Qué está haciendo cada competidor

### Vital Gym CR
Publicó un anuncio nuevo de entrenamiento personal [A1]. Parece un intento de
subir el ticket promedio.

## Movimientos que vale la pena mirar de cerca

El precio es el eje de la competencia [A2].

## Qué haría yo esta semana

Revisar su propia oferta de matrícula. Probar un anuncio con precio visible.
Medir consultas por WhatsApp. Sostener el mensaje dos semanas.
"""


class TestValidar(unittest.TestCase):
    def test_borrador_correcto_aprueba(self):
        r = validar.validar(BORRADOR_OK, ANUNCIOS)
        self.assertTrue(r["aprobado"], r["problemas"])
        self.assertEqual(r["cobertura_importantes"], "2/2")

    def test_detecta_datos_inventados(self):
        r = validar.validar(BORRADOR_OK + "\nInvirtió cerca de $2.000 este mes.", ANUNCIOS)
        self.assertFalse(r["aprobado"])
        self.assertTrue(any(p["tipo"] == "dato_inventado" for p in r["problemas"]))

    def test_detecta_metricas_que_no_existen(self):
        for palabra in ("impresiones", "CTR", "conversiones", "presupuesto de"):
            r = validar.validar(f"## Lo más importante\nTuvo muchas {palabra} [A1].", ANUNCIOS)
            self.assertFalse(r["aprobado"], f"debería rechazar '{palabra}'")

    def test_detecta_referencia_inventada(self):
        r = validar.validar(BORRADOR_OK.replace("[A2]", "[A9]"), ANUNCIOS)
        self.assertFalse(r["aprobado"])
        self.assertTrue(any(p["tipo"] == "referencia_inexistente" for p in r["problemas"]))

    def test_avisa_si_no_menciona_lo_importante(self):
        r = validar.validar("## Lo más importante\nNada pasó.", ANUNCIOS)
        self.assertTrue(any(a["tipo"] == "cobertura_incompleta" for a in r["avisos"]))

    def test_avisa_tono_exclamativo(self):
        r = validar.validar(BORRADOR_OK + "\n¡Excelente semana!", ANUNCIOS)
        self.assertTrue(any(a["tipo"] == "tono" for a in r["avisos"]))
