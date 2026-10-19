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

from jinja2 import Environment, FileSystemLoader

from .. import config

PLANTILLAS = Path(__file__).parent / "plantillas"


def contexto() -> dict[str, Any]:
    """Todo lo que la plantilla necesita, leído de config/."""
    from ..reporte import render

    datos = dict(config.config_landing())
    datos["planes"] = list(config.config_planes().get("planes") or [])
    # La landing muestra solo los visibles y el formulario ofrece solo los que
    # se cobran desde la web. Los ocultos siguen en `planes`: los clientes
    # que ya los tienen conservan precio y cadencia.
    datos["planes_visibles"] = [p for p in datos["planes"] if p.get("visible", True)]
    datos["planes_checkout"] = [p for p in datos["planes_visibles"] if p.get("checkout")]
    # El mismo plan que trae seleccionado el formulario: el resumen de al
    # lado tiene que decir lo mismo que el selector antes de que corra el JS.
    if datos["planes_checkout"]:
        primero = datos["planes_checkout"][0]
        datos.setdefault("plan_elegido", primero.get("clave"))
        datos.setdefault("ciclo_elegido", next(iter(primero.get("ciclos") or {}), None))
    datos.setdefault("marca", "PulseRival")
    datos.setdefault("contacto", {})
    # El mismo wordmark que va en el correo: una sola fuente para el logo.
    datos["logo_data_uri"] = render.logo_data_uri()
    # Por defecto las páginas apuntan a /marca/, que es donde las sirve Flask.
    # El generador estático lo reemplaza por la carpeta copiada al lado.
    datos.setdefault("marca_url", "/marca")
    return datos


# Las páginas públicas y el archivo estático de cada una. El formulario dejó
# de vivir en la portada: ahora tiene su propia página, a la que se llega desde
# los botones de cada plan, y es donde más adelante va a ir el cobro.
PAGINAS = (("index.html.j2", "index.html"), ("aplicar.html.j2", "aplicar.html"))


def entorno() -> Environment:
    """Jinja con cargador de archivos: las dos páginas comparten una base."""
    return Environment(loader=FileSystemLoader(str(PLANTILLAS)), autoescape=True)


def render(plantilla: str, datos: dict[str, Any]) -> str:
    return entorno().get_template(plantilla).render(**datos)


def carpeta_destino(destino: Path | str | None) -> Path:
    """Normaliza el destino a una carpeta.

    Cuando la landing era una sola página, `--destino` era la ruta del
    index.html. Ahora son varias páginas más los activos de marca, así que el
    destino es la carpeta. Se sigue aceptando una ruta a un .html para no
    romperle la mano a quien ya tenía el comando escrito: se usa su carpeta.
    """
    if not destino:
        return config.DIR_SALIDA / "landing"
    ruta = Path(destino)
    if ruta.suffix.lower() in (".html", ".htm"):
        return ruta.parent
    return ruta


def construir(destino: Path | str | None = None) -> list[Path]:
    """Escribe las páginas estáticas. Devuelve las rutas, en orden."""
    carpeta = carpeta_destino(destino)
    carpeta.mkdir(parents=True, exist_ok=True)
    base = contexto()
    # En estático los enlaces son archivos, no rutas del servidor: la página
    # tiene que funcionar abierta con doble clic desde una carpeta.
    base.update({"inicio_url": "index.html", "aplicar_url": "aplicar.html",
                 "marca_url": "marca"})
    # Los activos de marca se copian al lado del HTML: la página estática tiene
    # que funcionar abierta desde una carpeta, sin servidor.
    from .. import marca as marca_mod

    destino_marca = carpeta / "marca"
    destino_marca.mkdir(exist_ok=True)
    for activo in marca_mod.ACTIVOS.iterdir():
        if activo.is_file():
            (destino_marca / activo.name).write_bytes(activo.read_bytes())
    escritas = []
    for plantilla, archivo in PAGINAS:
        ruta = carpeta / archivo
        ruta.write_text(render(plantilla, base), encoding="utf-8")
        escritas.append(ruta)
    return escritas


def pendientes() -> list[str]:
    """Qué falta configurar antes de publicarla.

    Se avisa en vez de generar una página que parece lista y tiene los
    botones muertos.
    """
    datos = contexto()
    faltan = []
    contacto = datos.get("contacto") or {}
    if not (contacto.get("whatsapp") or contacto.get("email")):
        # El plan a la medida se vende por conversación y la landing solo
        # enlaza a ella: sin contacto, ese enlace no aparece.
        faltan.append(
            "config/landing.yaml: sin `contacto.whatsapp` ni `contacto.email`, el "
            "formulario estático no tiene a dónde mandar los datos y el enlace del "
            "plan a la medida (agencias) no aparece")
    if not datos["planes_visibles"]:
        faltan.append("config/planes.yaml: no hay ningún plan con `visible: true`")
    if not datos.get("dominio"):
        faltan.append("config/landing.yaml: falta `dominio` (solo sale en el pie)")
    return faltan
