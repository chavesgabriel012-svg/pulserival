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

from jinja2 import Environment

PLANTILLAS = Path(__file__).parent / "plantillas"

NEGRITA = re.compile(r"\*\*(.+?)\*\*")
ITALICA = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
CURSIVA_ = re.compile(r"_(.+?)_")
ENLACE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
REF = re.compile(r"\[(A\d+)\]")


def _inline(texto: str) -> str:
    """Formato dentro de una línea: negritas, itálicas, links y referencias.

    Los links se apartan antes de aplicar itálicas y se vuelven a poner
    después. Sin eso, un guion bajo dentro de una URL (frecuentísimo:
    `?utm_source=fb`, `?id=123_456`) se interpretaba como itálica y partía el
    href en dos, rompiendo justo los links de trazabilidad a la fuente.
    """
    t = html.escape(texto)

    apartados: list[str] = []

    def guardar(m: re.Match) -> str:
        etiqueta, url = m.group(1), m.group(2)
        apartados.append(
            f'<a href="{url}" style="color:#1a56db;text-decoration:none">{etiqueta}</a>'
        )
        return f"\x00{len(apartados) - 1}\x00"

    t = ENLACE.sub(guardar, t)
    t = NEGRITA.sub(r"<strong>\1</strong>", t)
    t = CURSIVA_.sub(r"<em>\1</em>", t)
    t = ITALICA.sub(r"<em>\1</em>", t)
    t = REF.sub(r'<span style="color:#6b7280;font-size:12px">[\1]</span>', t)
    for i, enlace in enumerate(apartados):
        t = t.replace(f"\x00{i}\x00", enlace)
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


# autoescape=True: los textos de los anuncios vienen de scrapers, no de acá.
# Un titular con "<" o una URL con comillas no debe poder romper el HTML del
# correo. Lo único que se inyecta tal cual es `cuerpo_html`, que ya salió
# escapado de markdown_a_html() y va marcado con |safe en la plantilla.
_ENTORNO = Environment(autoescape=True)


def email_html(reporte: dict[str, Any]) -> str:
    plantilla = _ENTORNO.from_string((PLANTILLAS / "reporte.html.j2").read_text(encoding="utf-8"))
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
    for f in reporte.get("senales") or []:
        donde = "Meta" if f["plataforma"] == "meta" else "Google"
        dias = f.get("dias_mensaje_mas_viejo")
        lineas.append(
            f"  {f['competidor']} [{donde}]: {f['mensajes']} mensajes en {f['piezas']} piezas"
            f" · {f['nuevos']} nuevos"
            + (f" · el más viejo lleva {dias} días" if dias is not None else "")
        )
    if reporte.get("senales"):
        lineas += ["", "-" * 60, ""]
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
        "QUÉ NO INCLUYE ESTE REPORTE",
        "No verá inversión, alcance, impresiones ni clics de la competencia. Meta y",
        "Google publican esos datos solo para anuncios políticos y para los entregados",
        "en la Unión Europea; para anuncios comerciales en Costa Rica no existen.",
        "",
        "Fuentes: Biblioteca de Anuncios de Meta y Centro de Transparencia de Anuncios",
        "de Google, ambas públicas y oficiales.",
        f"{reporte.get('marca') or 'PulseRival'} · Costa Rica",
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
