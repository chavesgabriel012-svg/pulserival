"""El paso humano de la Fase 1, hecho de forma que sirva para la Fase 2.

Flujo:
  1. `reporte exportar`  -> escribe un .md en borradores/ con el borrador.
  2. Vos lo editás en cualquier editor de texto.
  3. `reporte registrar` -> lee el archivo, calcula el diff contra el borrador
     original, le pone etiqueta y razón, y lo guarda en ediciones_registradas.

Ese diff es el activo del proyecto: cada semana acumulás ejemplos reales de
"la IA escribió esto, yo envié esto otro, por esto". Con 20 o 30 de esos se
pueden ajustar los prompts con evidencia en vez de con intuición (Fase 2), y
medir si el borrador necesita cada vez menos cambios (el indicador que habilita
la Fase 3).
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .. import config, db, util
from ..ia import Peticion, ProveedorError, ejecutar, prompts

INICIO_CUERPO = "<!-- ↓↓↓ EDITÁ LIBREMENTE DESDE ACÁ ↓↓↓ -->"
FIN_CUERPO = "<!-- ↑↑↑ HASTA ACÁ ↑↑↑ -->"
ETIQUETAS_VALIDAS = [
    "tono", "dato_incorrecto", "dato_faltante", "recorte", "reordenamiento",
    "recomendacion_debil", "jerga", "contexto_cliente", "formato", "sin_cambios", "otro",
]


# ── 1. exportar para editar ──────────────────────────────────────────
def exportar(con: sqlite3.Connection, reporte_id: int, carpeta: Path | None = None) -> Path:
    rep = db.fila(con, "SELECT r.*, c.nombre_empresa FROM reportes_generados r "
                       "JOIN clientes c ON c.id = r.cliente_id WHERE r.id = ?", (reporte_id,))
    if not rep:
        raise ValueError(f"No existe el reporte {reporte_id}")
    carpeta = carpeta or config.DIR_BORRADORES
    carpeta.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (rep["nombre_empresa"] or "cliente").lower()).strip("-")
    destino = carpeta / f"{rep['periodo_fin']}-{slug}-r{reporte_id}.md"

    val = db.leer_json(rep["validacion_json"], {}) or {}
    cuerpo = rep["final_md"] or rep["borrador_md"] or ""
    encabezado = [
        f"<!-- PulseRival · reporte #{reporte_id} · {rep['nombre_empresa']}",
        f"     Periodo: {rep['periodo_inicio']} al {rep['periodo_fin']}",
        f"     Generado con: {rep['proveedor_ia']}/{rep['modelo_ia']} ({rep['version_prompt']})",
        f"     Costo de IA: ${float(rep['costo_usd'] or 0):.4f}",
        f"     Asunto propuesto: {rep['asunto']}",
        "",
        f"     CONTROL DE CALIDAD: {'APROBADO' if val.get('aprobado') else 'REVISAR'} "
        f"· {val.get('palabras', '?')} palabras · cobertura {val.get('cobertura_importantes', '?')}",
    ]
    for p in val.get("problemas", []):
        encabezado.append(f"       ✗ {p['tipo']}: {p['detalle']}")
    for a in val.get("avisos", []):
        encabezado.append(f"       ! {a['tipo']}: {a['detalle']}")
    encabezado += [
        "",
        "     Editá el texto de abajo como quieras. Cuando termines, corré:",
        f"       python -m pulserival.cli reporte registrar --id {reporte_id} --etiqueta <etiqueta> --razon \"...\"",
        "     Este encabezado no se envía al cliente.",
        "-->",
        "",
        INICIO_CUERPO,
        "",
    ]
    destino.write_text("\n".join(encabezado) + cuerpo.strip() + f"\n\n{FIN_CUERPO}\n", encoding="utf-8")
    return destino


def leer_cuerpo(ruta: Path) -> str:
    texto = Path(ruta).read_text(encoding="utf-8")
    if INICIO_CUERPO in texto:
        texto = texto.split(INICIO_CUERPO, 1)[1]
    if FIN_CUERPO in texto:
        texto = texto.split(FIN_CUERPO, 1)[0]
    # por si quedaron comentarios HTML sueltos
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
    return texto.strip()


# ── 2. registrar lo que realmente enviaste ───────────────────────────
def registrar_final(
    con: sqlite3.Connection,
    reporte_id: int,
    ruta: Path | None = None,
    final_md: str | None = None,
    etiqueta: str | None = None,
    razon: str | None = None,
    autoetiquetar: bool = True,
) -> dict[str, Any]:
    rep = db.fila(con, "SELECT * FROM reportes_generados WHERE id = ?", (reporte_id,))
    if not rep:
        raise ValueError(f"No existe el reporte {reporte_id}")
    if rep["estado"] == "enviado":
        # Volver a 'revisado' desarmaría el único seguro contra enviar dos
        # veces el mismo reporte al cliente.
        raise RuntimeError(
            f"El reporte {reporte_id} ya se envió el {rep['enviado_en']}. No se puede "
            "registrar otra versión final: lo que salió ya salió. Si querés corregir "
            "algo, va en el reporte del periodo siguiente."
        )
    if final_md is None:
        if ruta is None:
            ruta = _buscar_borrador(reporte_id)
        final_md = leer_cuerpo(Path(ruta))
    borrador = (rep["borrador_md"] or "").strip()
    final_md = (final_md or "").strip()

    diff = "\n".join(
        difflib.unified_diff(
            borrador.splitlines(), final_md.splitlines(),
            fromfile="borrador_ia", tofile="enviado", lineterm="", n=2,
        )
    )
    similitud = difflib.SequenceMatcher(None, borrador, final_md).ratio()
    sin_cambios = similitud >= 0.999

    if not etiqueta:
        etiqueta = "sin_cambios" if sin_cambios else None
    if not etiqueta and autoetiquetar and diff:
        etiqueta, sugerida = _etiquetar_con_ia(con, diff, razon)
        razon = razon or sugerida
    etiqueta = etiqueta or "otro"

    edicion = {
        "reporte_id": reporte_id,
        "diff_unificado": diff or "(sin cambios)",
        "similitud": round(similitud, 4),
        "palabras_antes": util.contar_palabras(borrador),
        "palabras_despues": util.contar_palabras(final_md),
        "etiqueta": etiqueta,
        "razon": razon,
        "bloques_json": db.json_o_nada(_diff_por_seccion(borrador, final_md)),
    }
    edicion_id = db.insertar(con, "ediciones_registradas", edicion)
    db.actualizar(con, "reportes_generados", reporte_id,
                  {"final_md": final_md, "estado": "revisado"})
    return {"edicion_id": edicion_id, "similitud": similitud, "etiqueta": etiqueta,
            "cambios": not sin_cambios, "diff": diff}


def _buscar_borrador(reporte_id: int) -> Path:
    candidatos = sorted(config.DIR_BORRADORES.glob(f"*-r{reporte_id}.md"))
    if not candidatos:
        raise FileNotFoundError(
            f"No encontré el archivo del reporte {reporte_id} en {config.DIR_BORRADORES}. "
            "Corré primero: reporte exportar"
        )
    return candidatos[-1]


def _etiquetar_con_ia(con: sqlite3.Connection, diff: str, nota: str | None) -> tuple[str | None, str | None]:
    try:
        # Ojo: NO usar util.recortar acá; aplasta los saltos de línea y el
        # modelo necesita ver qué línea empieza con "-" y cuál con "+".
        sistema, usuario = prompts.armar("etiquetar_edicion", diff=_recortar_diff(diff), nota_editor=nota)
        resp = ejecutar(
            Peticion(tarea="etiquetar_edicion", sistema=sistema, usuario=usuario,
                     datos={"diff": diff}, json_estricto=True),
            con=con,
        )
        datos = resp.json({}) or {}
        etiqueta = datos.get("etiqueta")
        if etiqueta not in ETIQUETAS_VALIDAS:
            etiqueta = None
        razon = datos.get("razon") or datos.get("patron_evitable")
        return etiqueta, razon
    except (ProveedorError, FileNotFoundError):
        return None, None


def _recortar_diff(diff: str, largo: int = 6000) -> str:
    """Acorta el diff sin perder la estructura por líneas."""
    if len(diff) <= largo:
        return diff
    return diff[:largo].rsplit("\n", 1)[0] + "\n… (diff recortado)"


def _diff_por_seccion(antes: str, despues: str) -> list[dict[str, Any]]:
    """Cambios agrupados por sección (##). Sirve para ver qué parte del reporte
    reescribís siempre: es la pista más útil para arreglar el prompt."""
    def secciones(md: str) -> dict[str, str]:
        actual, mapa = "(inicio)", {}
        for linea in md.splitlines():
            if linea.startswith("## "):
                actual = linea[3:].strip()
                mapa.setdefault(actual, "")
            else:
                mapa[actual] = mapa.get(actual, "") + linea + "\n"
        return mapa

    a, b = secciones(antes), secciones(despues)
    salida = []
    for nombre in dict.fromkeys(list(a) + list(b)):
        ta, tb = a.get(nombre, ""), b.get(nombre, "")
        ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
        salida.append({
            "seccion": nombre,
            "similitud": round(ratio, 3),
            "estado": "igual" if ratio >= 0.999 else ("eliminada" if not tb else
                      "agregada" if not ta else "editada"),
            "palabras_antes": util.contar_palabras(ta),
            "palabras_despues": util.contar_palabras(tb),
        })
    return salida


# ── 3. exportar el dataset para la Fase 2 ────────────────────────────
def exportar_dataset(con: sqlite3.Connection, destino: Path | None = None) -> Path:
    """Un JSONL con (borrador, final, diff, etiqueta, razón, datos de entrada).

    Este archivo es lo que se usa en la Fase 2 para:
      - leer los 20 diffs y encontrar los patrones que se repiten;
      - reescribir los prompts con esa evidencia;
      - y, si algún día conviene, afinar un modelo.
    """
    destino = destino or (config.DIR_SALIDA / "dataset_ediciones.jsonl")
    destino.parent.mkdir(parents=True, exist_ok=True)
    filas = db.filas(
        con,
        """
        SELECT e.*, r.borrador_md, r.final_md, r.datos_json, r.version_prompt,
               r.proveedor_ia, r.modelo_ia, r.periodo_inicio, r.periodo_fin,
               c.nombre_empresa, c.industria
        FROM ediciones_registradas e
        JOIN reportes_generados r ON r.id = e.reporte_id
        JOIN clientes c ON c.id = r.cliente_id
        ORDER BY e.creado_en
        """,
    )
    with destino.open("w", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps({
                "reporte_id": f["reporte_id"],
                "cliente": f["nombre_empresa"],
                "industria": f["industria"],
                "periodo": [f["periodo_inicio"], f["periodo_fin"]],
                "generador": {"proveedor": f["proveedor_ia"], "modelo": f["modelo_ia"],
                              "version_prompt": f["version_prompt"]},
                "entrada": db.leer_json(f["datos_json"], {}),
                "borrador": f["borrador_md"],
                "final": f["final_md"],
                "diff": f["diff_unificado"],
                "similitud": f["similitud"],
                "etiqueta": f["etiqueta"],
                "razon": f["razon"],
                "bloques": db.leer_json(f["bloques_json"], []),
            }, ensure_ascii=False) + "\n")
    return destino


def metricas(con: sqlite3.Connection) -> dict[str, Any]:
    """¿Estoy editando cada vez menos? Es el indicador que dice cuándo se puede
    pasar a la Fase 3."""
    filas = db.filas(
        con,
        "SELECT e.similitud, e.etiqueta, r.periodo_fin FROM ediciones_registradas e "
        "JOIN reportes_generados r ON r.id = e.reporte_id ORDER BY e.creado_en",
    )
    if not filas:
        return {"reportes_revisados": 0}
    sims = [f["similitud"] or 0 for f in filas]
    etiquetas: dict[str, int] = {}
    for f in filas:
        etiquetas[f["etiqueta"] or "otro"] = etiquetas.get(f["etiqueta"] or "otro", 0) + 1
    ultimos = sims[-5:]
    return {
        "reportes_revisados": len(sims),
        "similitud_promedio": round(sum(sims) / len(sims), 3),
        "similitud_ultimos_5": round(sum(ultimos) / len(ultimos), 3),
        "etiquetas": dict(sorted(etiquetas.items(), key=lambda x: -x[1])),
        "listo_para_fase_3": len(sims) >= 8 and sum(ultimos) / len(ultimos) >= 0.9,
    }
