"""Convierte el Markdown del reporte en email HTML, texto plano y WhatsApp.

Se escribió un conversor mínimo de Markdown a propósito (en vez de instalar
una librería): el reporte usa solo títulos, listas, negritas y párrafos.
Menos dependencias = menos cosas que se rompan sin avisar.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from jinja2 import Template

PLANTILLAS = Path(__file__).parent / "plantillas"

NEGRITA = re.compile(r"\*\*(.+?)\*\*")
ITALICA = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
CURSIVA_ = re.compile(r"_(.+?)_")
ENLACE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
REF = re.compile(r"\[(A\d+)\]")


def _inline(texto: str) -> str:
    t = html.escape(texto)
    t = ENLACE.sub(r'<a href="\2" style="color:#1a56db;text-decoration:none">\1</a>', t)
    t = NEGRITA.sub(r"<strong>\1</strong>", t)
    t = CURSIVA_.sub(r"<em>\1</em>", t)
    t = ITALICA.sub(r"<em>\1</em>", t)
    t = REF.sub(r'<span style="color:#6b7280;font-size:12px">[\1]</span>', t)
    return t


def markdown_a_html(md: str) -> str:
    """Soporta: ## y ### títulos, - listas, párrafos, negrita, itálica, links."""
    salida: list[str] = []
    en_lista = False

    def cerrar_lista() -> None:
        nonlocal en_lista
        if en_lista:
            salida.append("</ul>")
            en_lista = False

    for linea in md.splitlines():
        cruda = linea.rstrip()
        if not cruda.strip():
            cerrar_lista()
            continue
        if cruda.startswith("### "):
            cerrar_lista()
            salida.append(
                '<h3 style="margin:22px 0 6px;font-size:16px;color:#111827">'
                f"{_inline(cruda[4:])}</h3>"
            )
        elif cruda.startswith("## "):
            cerrar_lista()
            salida.append(
                '<h2 style="margin:28px 0 8px;font-size:18px;color:#111827;'
                'border-bottom:1px solid #e5e7eb;padding-bottom:6px">'
                f"{_inline(cruda[3:])}</h2>"
            )
        elif cruda.startswith("# "):
            cerrar_lista()
            salida.append(f'<h1 style="font-size:20px;margin:0 0 10px">{_inline(cruda[2:])}</h1>')
        elif cruda.lstrip().startswith(("- ", "* ")):
            if not en_lista:
                salida.append('<ul style="margin:8px 0 8px 18px;padding:0">')
                en_lista = True
            salida.append(
                '<li style="margin:6px 0;line-height:1.55">'
                f"{_inline(cruda.lstrip()[2:])}</li>"
            )
        else:
            cerrar_lista()
            salida.append(
                '<p style="margin:10px 0;line-height:1.6;color:#374151">'
                f"{_inline(cruda)}</p>"
            )
    cerrar_lista()
    return "\n".join(salida)


def email_html(reporte: dict[str, Any]) -> str:
    plantilla = Template((PLANTILLAS / "reporte.html.j2").read_text(encoding="utf-8"))
    return plantilla.render(cuerpo_html=markdown_a_html(reporte["cuerpo_md"]), **reporte)


def email_texto(reporte: dict[str, Any]) -> str:
    """Versión en texto plano (algunos clientes de correo la prefieren y
    mejora la entregabilidad)."""
    lineas = [
        reporte.get("asunto", ""),
        f"{reporte.get('cliente','')} · periodo {reporte.get('periodo_inicio')} al {reporte.get('periodo_fin')}",
        "-" * 60,
        "",
    ]
    for linea in reporte["cuerpo_md"].splitlines():
        l = linea.rstrip()
        if l.startswith("## "):
            lineas += ["", l[3:].upper(), ""]
        elif l.startswith("### "):
            lineas += ["", l[4:], ""]
        else:
            lineas.append(NEGRITA.sub(r"\1", CURSIVA_.sub(r"\1", l)))
    lineas += [
        "",
        "-" * 60,
        "Fuentes: Biblioteca de Anuncios de Meta y Centro de Transparencia de "
        "Anuncios de Google, ambas públicas.",
        "PulseRival · Costa Rica",
    ]
    return "\n".join(lineas)


def whatsapp(reporte: dict[str, Any], max_puntos: int = 5) -> str:
    """Resumen cortito, listo para copiar y pegar en WhatsApp."""
    cuerpo = reporte["cuerpo_md"]
    lineas = [f"*{reporte.get('cliente','')}* — anuncios de la competencia",
              f"Periodo: {reporte.get('periodo_inicio')} al {reporte.get('periodo_fin')}", ""]
    seccion = None
    puntos: list[str] = []
    for linea in cuerpo.splitlines():
        l = linea.strip()
        if l.startswith("## "):
            seccion = l[3:]
            continue
        if not l or l.startswith("#"):
            continue
        if seccion and "importante" in seccion.lower() and len(puntos) < max_puntos:
            limpio = REF.sub("", NEGRITA.sub(r"*\1*", l))
            puntos.append(re.sub(r"\s+([.,;])", r"\1", limpio).strip())
    lineas += [p for p in puntos if p]
    lineas += ["", "El reporte completo va por correo."]
    return "\n".join(lineas)
