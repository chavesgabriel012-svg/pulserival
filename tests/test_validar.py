"""El control de calidad: lo que hace posible revisar rápido (y automatizar)."""
from __future__ import annotations

import unittest

from pulserival.reporte import validar

ANUNCIOS = [
    {"referencia": "[A1]", "clasificacion": "nuevo"},
    {"referencia": "[A2]", "clasificacion": "cambiado"},
    {"referencia": "[A3]", "clasificacion": "continua"},
]

BORRADOR_OK = """## Resumen ejecutivo

Vital Gym bajó el precio de su plan mensual [A1] y cambió el texto de su
anuncio de matrícula [A2].

## Qué está haciendo cada competidor

### Vital Gym CR
Publicó un anuncio nuevo de entrenamiento personal [A1]. Parece un intento de
subir el ticket promedio.

## Panorama de la competencia

Vital Gym es el único con movimiento [A1].

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


class TestAnunciosSinTexto(unittest.TestCase):
    """Google no publica el texto de los anuncios, y los catálogos dinámicos
    de Meta devuelven plantillas ({{product.brand}}) en vez de mensaje.

    De esos anuncios sabemos que existen, su formato y sus fechas — nada más.
    Si el modelo describe lo que 'dicen', lo está inventando, y el cliente lo
    puede comprobar en un clic porque el anexo linkea a la fuente.
    """

    ANUNCIOS = [
        {"referencia": "[A1]", "clasificacion": "nuevo", "sin_texto": True},
        {"referencia": "[A2]", "clasificacion": "nuevo", "sin_texto": False},
    ]

    def test_rechaza_que_se_describa_el_mensaje_de_un_anuncio_sin_texto(self):
        for verbo in ("promete", "ofrece", "anuncia", "destaca"):
            with self.subTest(verbo):
                r = validar.validar(
                    f"## Lo más importante\nEl anuncio {verbo} un descuento [A1]. Y otro [A2].",
                    self.ANUNCIOS)
                self.assertFalse(r["aprobado"])
                self.assertTrue(any(p["tipo"] == "mensaje_inventado" for p in r["problemas"]))

    def test_acepta_hablar_de_formato_y_fechas(self):
        r = validar.validar(
            "## Resumen ejecutivo\n"
            "Mantiene un anuncio en video corriendo desde agosto [A1].\n"
            "## Panorama de la competencia\nUn solo competidor con movimiento [A2].\n"
            "## Qué está haciendo cada competidor\n### X\nBajó el precio [A2].\n"
            "## Movimientos que vale la pena mirar de cerca\nNada.\n"
            "## Qué haría yo esta semana\nRevisar la oferta propia esta semana con calma.",
            self.ANUNCIOS)
        self.assertTrue(r["aprobado"], r["problemas"])

    def test_el_anuncio_con_texto_si_puede_describirse(self):
        r = validar.validar("## Lo más importante\nEl anuncio ofrece 2x1 [A2].", self.ANUNCIOS)
        self.assertFalse(any(p["tipo"] == "mensaje_inventado" for p in r["problemas"]))


class TestTonoConCitas(unittest.TestCase):
    def test_no_se_marca_la_exclamacion_del_anuncio_citado(self):
        r = validar.validar(
            '## Resumen ejecutivo\nEl anuncio dice "¡Matrícula gratis!" y apunta a captación [A1].',
            [{"referencia": "[A1]", "clasificacion": "nuevo"}])
        self.assertFalse(any(a["tipo"] == "tono" for a in r["avisos"]),
                         "el signo es del competidor, no del analista")

    def test_si_se_marca_la_exclamacion_del_analista(self):
        r = validar.validar("## Resumen ejecutivo\nExcelente semana para el cliente!",
                            [{"referencia": "[A1]", "clasificacion": "nuevo"}])
        self.assertTrue(any(a["tipo"] == "tono" for a in r["avisos"]))


class TestReferenciasAgrupadas(unittest.TestCase):
    """El modelo agrupa referencias: "[A16, A24, A27]".

    La expresión original solo reconocía [A16]. En un reporte real eso dejó
    32 referencias sin validar: una inventada dentro de un grupo no se habría
    detectado.
    """

    ANUNCIOS = [{"referencia": f"[A{i}]", "clasificacion": "nuevo"} for i in (1, 2, 3)]

    def test_valida_las_referencias_dentro_de_un_grupo(self):
        r = validar.validar("## Resumen ejecutivo\nDatos [A1, A2, A3].", self.ANUNCIOS)
        self.assertEqual(sorted(r["referencias_usadas"]), ["[A1]", "[A2]", "[A3]"])

    def test_detecta_una_referencia_inventada_dentro_de_un_grupo(self):
        r = validar.validar("## Resumen ejecutivo\nDatos [A1, A99].", self.ANUNCIOS)
        self.assertFalse(r["aprobado"])
        self.assertTrue(any("[A99]" in p["detalle"] for p in r["problemas"]))


class TestCompetidorSinDatos(unittest.TestCase):
    """De un competidor sin un solo anuncio no se puede afirmar nada.

    Real: el reporte recomendó "aumentar la pauta en zonas fuera del GAM
    donde Artelec suele tener presencia física". Los datos no decían nada de
    las tiendas de Artelec; salió del conocimiento general del modelo.
    """

    ANUNCIOS = [{"referencia": "[A1]", "clasificacion": "nuevo", "competidor": "Monge"}]
    COMPETIDORES = ["Monge", "Artelec"]

    def test_rechaza_afirmaciones_sobre_un_competidor_sin_anuncios(self):
        r = validar.validar(
            "## Resumen ejecutivo\nArtelec tiene presencia física fuera del GAM.",
            self.ANUNCIOS, self.COMPETIDORES)
        self.assertFalse(r["aprobado"])
        self.assertTrue(any(p["tipo"] == "conocimiento_externo" for p in r["problemas"]))

    def test_acepta_decir_que_no_registra_actividad(self):
        r = validar.validar(
            "## Resumen ejecutivo\nArtelec no registra actividad publicitaria en el periodo.",
            self.ANUNCIOS, self.COMPETIDORES)
        self.assertFalse(any(p["tipo"] == "conocimiento_externo" for p in r["problemas"]))

    def test_no_molesta_con_los_competidores_que_si_tienen_datos(self):
        r = validar.validar("## Resumen ejecutivo\nMonge tiene 7 anuncios [A1].",
                            self.ANUNCIOS, self.COMPETIDORES)
        self.assertFalse(any(p["tipo"] == "conocimiento_externo" for p in r["problemas"]))


class TestCoberturaSobreLoQueElModeloVio(unittest.TestCase):
    def test_no_se_reclama_un_anuncio_que_el_modelo_nunca_recibio(self):
        todos = [{"referencia": f"[A{i}]", "clasificacion": "nuevo"} for i in range(1, 100)]
        vistos = todos[:3]
        r = validar.validar("## Resumen ejecutivo\nTodo citado [A1, A2, A3].",
                            todos, anuncios_vistos=vistos)
        self.assertEqual(r["cobertura_importantes"], "3/3")
        self.assertFalse(any(a["tipo"] == "cobertura_incompleta" for a in r["avisos"]))


class TestPalabrasCompletas(unittest.TestCase):
    """El validador busca palabras, no subcadenas.

    Real: marcó como conocimiento externo la frase "Artelec se mantiene
    completamente ausente", que es exactamente lo que sí se puede decir de un
    competidor sin anuncios. El culpable era "tiene" dentro de "mantiene".
    """

    ANUNCIOS = [{"referencia": "[A1]", "clasificacion": "nuevo", "competidor": "Monge"}]
    COMPETIDORES = ["Monge", "Artelec"]

    def test_mantiene_ausente_es_una_frase_valida(self):
        r = validar.validar(
            "## Resumen ejecutivo\nArtelec se mantiene completamente ausente de las "
            "plataformas en este periodo.\n## Qué haría yo esta semana\nAproveche el espacio.",
            self.ANUNCIOS, self.COMPETIDORES)
        self.assertFalse(any(p["tipo"] == "conocimiento_externo" for p in r["problemas"]),
                         "'mantiene' no es 'tiene'")

    def test_sigue_detectando_la_afirmacion_de_verdad(self):
        r = validar.validar(
            "## Resumen ejecutivo\nArtelec tiene sucursales en todo el país.",
            self.ANUNCIOS, self.COMPETIDORES)
        self.assertTrue(any(p["tipo"] == "conocimiento_externo" for p in r["problemas"]))


class TestSeccionesObligatorias(unittest.TestCase):
    """Sin recomendaciones, el reporte no sirve.

    Real: un borrador salió de 562 palabras y sin la sección "Qué haría yo",
    y el control lo dejó pasar como simple aviso. Es la sección por la que el
    cliente paga."""

    ANUNCIOS = [{"referencia": "[A1]", "clasificacion": "nuevo", "competidor": "Monge"}]

    def test_faltar_las_recomendaciones_bloquea_el_reporte(self):
        r = validar.validar(
            "## Resumen ejecutivo\nMonge lanzó tres anuncios [A1].\n"
            "## Panorama de la competencia\nDetalle.\n"
            "## Qué está haciendo cada competidor\nDetalle.\n"
            "## Movimientos que vale la pena mirar de cerca\nNada.", self.ANUNCIOS)
        self.assertFalse(r["aprobado"])
        self.assertTrue(any(p["tipo"] == "seccion_faltante" for p in r["problemas"]))

    def test_una_seccion_secundaria_solo_avisa(self):
        r = validar.validar(
            "## Resumen ejecutivo\nMonge lanzó tres anuncios [A1].\n"
            "## Qué haría yo esta semana\nRevise su oferta de financiamiento esta semana.",
            self.ANUNCIOS)
        self.assertTrue(any(a["tipo"] == "seccion_faltante" for a in r["avisos"]))
        self.assertFalse(any(p["tipo"] == "seccion_faltante" for p in r["problemas"]))
