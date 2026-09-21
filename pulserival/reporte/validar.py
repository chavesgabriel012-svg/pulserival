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
    "lo mas importante",
    "que esta haciendo cada competidor",
    "movimientos",
    "que haria yo",
]
REF = re.compile(r"\[A(\d+)\]")


def validar(borrador: str, anuncios: list[dict[str, Any]]) -> dict[str, Any]:
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
    usadas = {f"[A{n}]" for n in REF.findall(borrador)}
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

    # 4. cobertura de lo importante
    importantes = [a for a in anuncios if a["clasificacion"] in ("nuevo", "cambiado")]
    sin_mencion = [a["referencia"] for a in importantes if a["referencia"] not in usadas]
    if sin_mencion:
        avisos.append({
            "tipo": "cobertura_incompleta",
            "detalle": "Anuncios nuevos o cambiados que el borrador no menciona: "
                       + ", ".join(sin_mencion),
        })

    # 5. forma
    for seccion in SECCIONES_ESPERADAS:
        if seccion not in plano:
            avisos.append({"tipo": "seccion_faltante", "detalle": f"Falta la sección '{seccion}'."})
    palabras = util.contar_palabras(borrador)
    if palabras < 120:
        avisos.append({"tipo": "muy_corto", "detalle": f"Solo {palabras} palabras."})
    if palabras > 900:
        avisos.append({"tipo": "muy_largo", "detalle": f"{palabras} palabras; el cliente lo lee en el celular."})
    if "!" in borrador:
        avisos.append({"tipo": "tono", "detalle": "Hay signos de exclamación; el reporte va en tono sobrio."})

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
