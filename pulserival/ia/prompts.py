"""Carga los prompts desde archivos .md.

Los prompts viven en archivos de texto, no en el código, para que puedas
ajustar la redacción sin tocar Python. Cada archivo tiene dos secciones:
"# SISTEMA" (las reglas) y "# USUARIO" (los datos, con plantilla Jinja2).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from jinja2 import Template

DIR = Path(__file__).parent / "prompts"


def cargar(tarea: str) -> tuple[str, str]:
    ruta = DIR / f"{tarea}.md"
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el prompt {ruta}")
    texto = ruta.read_text(encoding="utf-8")
    if "# USUARIO" not in texto:
        raise ValueError(f"El prompt {ruta.name} necesita una sección '# USUARIO'")
    sistema, usuario = texto.split("# USUARIO", 1)
    sistema = sistema.replace("# SISTEMA", "", 1).strip()
    return sistema, usuario.strip()


def armar(tarea: str, **contexto) -> tuple[str, str]:
    """Devuelve (sistema, usuario) con los datos ya metidos en la plantilla."""
    sistema, plantilla = cargar(tarea)
    return sistema, Template(plantilla, trim_blocks=False, lstrip_blocks=False).render(**contexto)


def version(tarea: str) -> str:
    """Huella del prompt. Queda guardada con cada reporte: si mañana cambiás
    el prompt, sabés qué reporte salió con qué versión."""
    ruta = DIR / f"{tarea}.md"
    h = hashlib.sha256(ruta.read_bytes()).hexdigest()[:8]
    return f"{tarea}@{h}"
