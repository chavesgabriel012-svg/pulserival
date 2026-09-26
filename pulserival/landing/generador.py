"""Genera la landing como un HTML estático.

Por qué estática y no servida por una aplicación: en esta etapa no hay
servidor y no hace falta. Los planes y los textos viven en config/, la página
se genera con Jinja2 —que ya es dependencia del proyecto por los correos— y
el resultado se sube a cualquier hosting de archivos, que es gratis o casi.

El formulario no necesita backend tampoco: arma un mensaje de WhatsApp con
los datos ya ordenados. Cuando exista el servidor con panel y webhook, ese
mismo formulario pasa a hacer POST y el resto de la página no cambia.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment

from .. import config

PLANTILLAS = Path(__file__).parent / "plantillas"


def contexto() -> dict[str, Any]:
    """Todo lo que la plantilla necesita, leído de config/."""
    from ..reporte import render

    datos = dict(config.config_landing())
    datos["planes"] = list(config.config_planes().get("planes") or [])
    datos.setdefault("marca", "PulseRival")
    datos.setdefault("contacto", {})
    # El mismo wordmark que va en el correo: una sola fuente para el logo.
    datos["logo_data_uri"] = render.logo_data_uri()
    return datos


def construir(destino: Path | None = None) -> Path:
    entorno = Environment(autoescape=True)
    plantilla = entorno.from_string(
        (PLANTILLAS / "index.html.j2").read_text(encoding="utf-8"))
    html = plantilla.render(**contexto())
    destino = destino or (config.DIR_SALIDA / "landing" / "index.html")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    return destino


def pendientes() -> list[str]:
    """Qué falta configurar antes de publicarla.

    Se avisa en vez de generar una página que parece lista y tiene los
    botones muertos.
    """
    datos = contexto()
    faltan = []
    contacto = datos.get("contacto") or {}
    if not (contacto.get("whatsapp") or contacto.get("email")):
        faltan.append(
            "config/landing.yaml: sin `contacto.whatsapp` ni `contacto.email`, "
            "el formulario no tiene a dónde mandar los datos")
    sin_enlace = [p["clave"] for p in datos["planes"]
                  if p.get("precio_usd") and not p.get("enlace_pago")]
    if sin_enlace:
        faltan.append(
            f"config/planes.yaml: los planes {', '.join(sin_enlace)} no tienen "
            "`enlace_pago`, así que muestran 'Hablemos' en vez de cobrar. "
            "El enlace se crea en el panel de Tilopay (ruta No-Code) o de ONVO "
            "(ONVO Link) y se pega ahí")
    if not datos.get("dominio"):
        faltan.append("config/landing.yaml: falta `dominio` (solo sale en el pie)")
    return faltan
