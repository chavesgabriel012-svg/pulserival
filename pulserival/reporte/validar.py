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
REF_NUM = re.compile(r"A(\d+)")


def validar(borrador: str, anuncios: list[dict[str, Any]],
            competidores: list[str] | None = None,
            anuncios_vistos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Revisa el borrador contra los datos.

    `anuncios` son todos los del periodo; `anuncios_vistos` los que entraron
    al prompt. La cobertura se mide contra los segundos: reclamarle al modelo
    que no citó un anuncio que nunca vio no es un hallazgo, es ruido.
    """
    problemas: list[dict[str, str]] = []
    avisos: list[dict[str, str]] = []
    plano = util.normalizar_texto(borrador)

    # 1. datos inventados
    for palabra in PROHIBIDAS:
        if util.normalizar_texto(palabra) in plano:
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
                    "no pauta", "no registro", "inactivo", "no hubo")):
                continue
            if any(util.normalizar_texto(p) in plano_oracion for p in
                   ("tiene", "suele", "presencia", "tiendas", "sucursales", "sede",
                    "clientes", "cobertura", "mercado", "posicion")):
                problemas.append({
                    "tipo": "conocimiento_externo",
                    "detalle": f"No hay un solo anuncio de {nombre} en los datos, pero el "
                               f"borrador afirma algo sobre esa empresa: \"{util.recortar(oracion, 120)}\". "
                               "Eso no sale de la fuente.",
                })
                break

    # 5. cobertura de lo importante
    base = anuncios_vistos if anuncios_vistos is not None else anuncios
    importantes = [a for a in base if a["clasificacion"] in ("nuevo", "cambiado")]
    sin_mencion = [a["referencia"] for a in importantes if a["referencia"] not in usadas]
    if sin_mencion:
        avisos.append({
            "tipo": "cobertura_incompleta",
            "detalle": "Anuncios nuevos o cambiados que el borrador no menciona: "
                       + ", ".join(sin_mencion),
        })

    # 6. forma
    for seccion in SECCIONES_ESPERADAS:
        if seccion not in plano:
            avisos.append({"tipo": "seccion_faltante", "detalle": f"Falta la sección '{seccion}'."})
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
