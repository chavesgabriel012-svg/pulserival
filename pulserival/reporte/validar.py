"""Control de calidad automático del borrador.

Es la pieza que hace posible la Fase 3 (envío casi sin intervención). Si el
borrador pasa estos chequeos, tu revisión puede ser un "spot check" de 2
minutos; si no pasa, el sistema te dice exactamente qué revisar.

Lo que revisa:
  1. Que no invente datos que la fuente no tiene (inversión, alcance, clics).
  2. Que toda referencia [A#] exista de verdad en los datos del periodo.
  3. Que no haya anuncios importantes (nuevos/cambiados) sin mencionar.
  4. Que tenga las secciones esperadas y un largo razonable.
"""
from __future__ import annotations

import re
from typing import Any

from .. import util

# Palabras que implican datos que la biblioteca pública NO entrega.
# Si aparecen, casi siempre es el modelo inventando.
PROHIBIDAS = [
    "invirtió", "invirtio", "invierte", "inversión de", "inversion de",
    "presupuesto de", "gastó", "gasto de", "cpm", "cpc", "roas",
    "impresiones", "alcance de", "clics", "conversiones", "ctr",
    "engagement de", "tasa de conversión", "retorno de inversión",
]
SECCIONES_OBLIGATORIAS = ["resumen ejecutivo", "que haria yo"]
SECCIONES_ESPERADAS = [
    "resumen ejecutivo",
    "panorama de la competencia",
    "que esta haciendo cada competidor",
    "movimientos",
    "que haria yo",
]
# El modelo agrupa referencias: "[A16, A24, A27]". Con la expresión vieja,
# que solo reconocía [A16], esas 32 referencias de un reporte real pasaban
# sin validar: una inventada ahí dentro no se habría detectado.
REF = re.compile(r"\[\s*A\d+(?:\s*,\s*A\d+)*\s*\]")


def _menciona(texto_plano: str, palabras: tuple[str, ...]) -> bool:
    """Busca palabras completas, no subcadenas.

    Sin esto, "tiene" coincidía dentro de "mantiene", "sostiene" y "obtiene",
    y el validador rechazaba frases correctas.
    """
    return any(re.search(rf"\b{re.escape(p)}\b", texto_plano) for p in palabras)
REF_NUM = re.compile(r"A(\d+)")
# Texto de relleno: una sección puede existir como título y no decir nada.
# El respaldo sin IA escribe exactamente esto, y el reporte pasaba aprobado.
RELLENO = ("pendiente de revision", "por definir", "sin informacion",
           "completar", "a revisar")
# Un umbral de palabras es a ojo, así que una sección flaca solo avisa.
# Lo que bloquea es el relleno, que es el fallo que de verdad se vio.
MINIMO_POR_SECCION = 15   # palabras


def _cuerpo_de_seccion(borrador: str, seccion: str) -> str | None:
    """El texto bajo un encabezado, hasta el siguiente encabezado.

    Devuelve None si el encabezado no existe.
    """
    cuerpo: list[str] = []
    encontrado = dentro = False
    for linea in borrador.splitlines():
        if linea.lstrip().startswith("#"):
            if dentro:
                break
            dentro = seccion in util.normalizar_texto(linea)
            encontrado = encontrado or dentro
            continue
        if dentro:
            cuerpo.append(linea)
    return "\n".join(cuerpo) if encontrado else None


def validar(borrador: str, anuncios: list[dict[str, Any]],
            competidores: list[str] | None = None,
            anuncios_vistos: list[dict[str, Any]] | None = None,
            proveedor: str | None = None) -> dict[str, Any]:
    """Revisa el borrador contra los datos.

    `anuncios` son todos los del periodo; `anuncios_vistos` los que entraron
    al prompt. La cobertura se mide contra los segundos: reclamarle al modelo
    que no citó un anuncio que nunca vio no es un hallazgo, es ruido.

    `proveedor` es quién escribió el borrador. Si fue el respaldo del código,
    no hay interpretación que validar y el reporte no se aprueba.
    """
    problemas: list[dict[str, str]] = []
    avisos: list[dict[str, str]] = []
    plano = util.normalizar_texto(borrador)

    # 0. escrito por el respaldo del código, sin interpretación
    # Pasó en una corrida real: los cuatro modelos de la cadena habían dejado
    # de existir, el respaldo armó los conteos y el control de calidad lo dio
    # por APROBADO. Un reporte sin análisis no es el producto.
    if proveedor == "stub":
        problemas.append({
            "tipo": "sin_interpretacion",
            "detalle": "El borrador lo escribió el respaldo del código, no un modelo de IA: "
                       "tiene los conteos correctos pero ningún análisis. Revisá "
                       "`cli modelos` y `cli diagnostico` antes de regenerarlo.",
        })

    # 1. datos inventados
    # Palabras completas, no subcadenas: "ctr" vive dentro de
    # "electrodomesticos", así que a un cliente de línea blanca se le
    # bloqueaban TODOS los reportes por una métrica que nadie escribió.
    for palabra in PROHIBIDAS:
        if re.search(rf"\b{re.escape(util.normalizar_texto(palabra))}\b", plano):
            problemas.append({
                "tipo": "dato_inventado",
                "detalle": f"El borrador menciona '{palabra}'. La biblioteca pública de "
                           "anuncios no entrega ese dato: hay que borrarlo o reformularlo.",
            })

    # 2. referencias
    validas = {a["referencia"] for a in anuncios}
    usadas = {f"[A{n}]" for grupo in REF.findall(borrador) for n in REF_NUM.findall(grupo)}
    for r in sorted(usadas - validas):
        problemas.append({"tipo": "referencia_inexistente",
                          "detalle": f"Usa la referencia {r}, que no corresponde a ningún anuncio."})

    # 3. anuncios sin texto: no se puede hablar de lo que dicen
    VERBOS_DE_MENSAJE = [
        "dice", "promete", "ofrece", "anuncia", "comunica", "menciona",
        "destaca", "apunta a", "habla de", "propone", "invita a",
    ]
    for a in anuncios:
        if not a.get("sin_texto"):
            continue
        ref = a["referencia"]
        for oracion in re.split(r"(?<=[.!?])\s+", borrador):
            if ref not in oracion:
                continue
            plano_oracion = util.normalizar_texto(oracion)
            for verbo in VERBOS_DE_MENSAJE:
                if util.normalizar_texto(verbo) in plano_oracion:
                    problemas.append({
                        "tipo": "mensaje_inventado",
                        "detalle": f"{ref} es un anuncio del que la fuente NO publica el texto, "
                                   f"pero el borrador describe lo que dice ('{verbo}'). "
                                   "De esos anuncios solo se puede reportar formato, fechas y "
                                   "actividad.",
                    })
                    break
            break

    # 4. competidores sin un solo anuncio: no se puede afirmar nada de ellos
    con_datos = {a.get("competidor") for a in anuncios if a.get("competidor")}
    for nombre in (competidores or []):
        if nombre in con_datos or not nombre:
            continue
        for oracion in re.split(r"(?<=[.!?])\s+", borrador):
            if nombre.lower() not in oracion.lower():
                continue
            plano_oracion = util.normalizar_texto(oracion)
            # "no registra actividad" es lo único que se puede decir; cualquier
            # otra afirmación sale de conocimiento externo, no de la fuente.
            if any(util.normalizar_texto(p) in plano_oracion for p in
                   ("no registra", "no tiene anuncios", "sin actividad", "no aparece",
                    "no pauta", "no registro", "inactivo", "no hubo", "ausente",
                    "no registro movimientos", "no pauto")):
                continue
            # Palabras completas. Buscar "tiene" como subcadena marcaba
            # "se mantiene ausente", que es justo lo que sí se puede decir.
            if _menciona(plano_oracion, ("tiene", "suele", "presencia", "tiendas",
                                         "sucursales", "sede", "clientes", "cobertura",
                                         "mercado", "posicion", "atiende", "opera")):
                problemas.append({
                    "tipo": "conocimiento_externo",
                    "detalle": f"No hay un solo anuncio de {nombre} en los datos, pero el "
                               f"borrador afirma algo sobre esa empresa: \"{util.recortar(oracion, 120)}\". "
                               "Eso no sale de la fuente.",
                })
                break

    # 4b. recomendaciones apoyadas en la ausencia de un competidor
    # Que no hayamos detectado anuncios no prueba que no esté pautando. Un
    # reporte real recomendó "aproveche la ausencia total de pauta de
    # Artelec": Artelec sí pautaba, el scraper no la había encontrado, y el
    # cliente habría invertido sobre un hueco inexistente.
    sin_datos = [n for n in (competidores or []) if n and n not in con_datos]
    if sin_datos:
        cuerpo = _cuerpo_de_seccion(borrador, "que haria yo") or ""
        for oracion in re.split(r"(?<=[.!?])\s+|\n", cuerpo):
            plano_oracion = util.normalizar_texto(oracion)
            nombrado = next((n for n in sin_datos if n.lower() in oracion.lower()), None)
            if not nombrado:
                continue
            if _menciona(plano_oracion, ("ausencia", "ausente", "vacio", "hueco",
                                         "no pauta", "no esta pautando", "silencio",
                                         "abandonado", "desaparecio")):
                problemas.append({
                    "tipo": "recomendacion_sobre_ausencia",
                    "detalle": f"Una recomendación se apoya en que {nombrado} no aparece: "
                               f"\"{util.recortar(oracion.strip(), 120)}\". No haber detectado "
                               "anuncios no prueba que no esté pautando; puede ser que la "
                               "recolección no lo encontró.",
                })
                break

    # 5. cobertura de lo importante
    base = anuncios_vistos if anuncios_vistos is not None else anuncios
    importantes = [a for a in base if a["clasificacion"] in ("nuevo", "cambiado")]
    sin_mencion = [a["referencia"] for a in importantes if a["referencia"] not in usadas]
    if importantes and not usadas:
        # Ninguna referencia con anuncios que referenciar no es cobertura
        # incompleta: es que no hay análisis de los anuncios del periodo.
        problemas.append({
            "tipo": "sin_referencias",
            "detalle": f"Hay {len(importantes)} anuncios nuevos o cambiados y el borrador no "
                       "cita ni uno. Sin referencias [A#] no se puede verificar nada de lo "
                       "que dice.",
        })
    elif sin_mencion:
        avisos.append({
            "tipo": "cobertura_incompleta",
            "detalle": "Anuncios nuevos o cambiados que el borrador no menciona: "
                       + ", ".join(sin_mencion),
        })

    # 6. forma
    for seccion in SECCIONES_ESPERADAS:
        if seccion in plano:
            continue
        # Sin resumen ejecutivo ni recomendaciones el reporte no sirve: son
        # las dos secciones por las que el cliente paga. El resto es aviso.
        destino = problemas if seccion in SECCIONES_OBLIGATORIAS else avisos
        destino.append({"tipo": "seccion_faltante",
                        "detalle": f"Falta la sección '{seccion}'."})
    for seccion in SECCIONES_OBLIGATORIAS:
        cuerpo = _cuerpo_de_seccion(borrador, seccion)
        if cuerpo is None:
            continue                      # ya se reportó como faltante
        plano_cuerpo = util.normalizar_texto(cuerpo)
        cuenta = util.contar_palabras(cuerpo)
        if any(r in plano_cuerpo for r in RELLENO):
            problemas.append({
                "tipo": "seccion_sin_escribir",
                "detalle": f"La sección '{seccion}' tiene texto de relleno en vez de "
                           "contenido; es una de las dos por las que el cliente paga.",
            })
        elif cuenta < MINIMO_POR_SECCION:
            avisos.append({
                "tipo": "seccion_flaca",
                "detalle": f"La sección '{seccion}' tiene {cuenta} palabras.",
            })
    palabras = util.contar_palabras(borrador)
    if palabras < 350:
        avisos.append({"tipo": "muy_corto", "detalle": f"Solo {palabras} palabras."})
    if palabras > 1500:
        avisos.append({"tipo": "muy_largo",
                       "detalle": f"{palabras} palabras; se vuelve pesado de leer."})
    # Los signos de exclamación del texto citado de un anuncio son del
    # competidor, no nuestros: solo se revisa la prosa del analista.
    sin_citas = re.sub(r'[“"«][^”"»]{0,400}[”"»]', " ", borrador)
    if "!" in sin_citas or "¡" in sin_citas:
        avisos.append({"tipo": "tono",
                       "detalle": "Hay signos de exclamación fuera de texto citado; "
                                  "el reporte va en tono sobrio."})

    return {
        "aprobado": not problemas,
        "problemas": problemas,
        "avisos": avisos,
        "palabras": palabras,
        "referencias_usadas": sorted(usadas),
        "cobertura_importantes": f"{len(importantes) - len(sin_mencion)}/{len(importantes)}",
    }


def formatear(resultado: dict[str, Any]) -> str:
    lineas = []
    estado = "APROBADO" if resultado["aprobado"] else "REVISAR"
    lineas.append(f"Control de calidad: {estado} · {resultado['palabras']} palabras · "
                  f"cobertura {resultado['cobertura_importantes']}")
    for p in resultado["problemas"]:
        lineas.append(f"  ✗ [{p['tipo']}] {p['detalle']}")
    for a in resultado["avisos"]:
        lineas.append(f"  ! [{a['tipo']}] {a['detalle']}")
    return "\n".join(lineas)
