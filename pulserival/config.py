"""Configuración: lee el archivo .env y los YAML de config/.

No usa librerías externas para el .env a propósito: menos cosas que instalar,
menos cosas que se rompan.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parent.parent
DIR_CONFIG = RAIZ / "config"
DIR_DATOS = RAIZ / "datos"
DIR_BORRADORES = RAIZ / "borradores"
DIR_SALIDA = RAIZ / "salida"


_ENV_CARGADOS: set[Path] = set()


def cargar_env(ruta: Path | None = None) -> None:
    """Carga variables de .env al entorno, sin sobreescribir las que ya existen.

    Se hace una sola vez por archivo: `env()` se llama muchas veces por corrida
    y releer el archivo cada vez no aporta nada (os.environ ya tiene el valor).
    """
    ruta = ruta or (RAIZ / ".env")
    if ruta in _ENV_CARGADOS:
        return
    if not ruta.exists():
        return
    _ENV_CARGADOS.add(ruta)
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave, valor = clave.strip(), valor.strip().strip('"').strip("'")
        os.environ.setdefault(clave, valor)


def env(clave: str, defecto: str | None = None) -> str | None:
    cargar_env()
    valor = os.environ.get(clave, defecto)
    return valor or None


def ruta_db() -> Path:
    cargar_env()
    return Path(os.environ.get("PULSERIVAL_DB") or (DIR_DATOS / "pulserival.db"))


@lru_cache(maxsize=None)
def _yaml(nombre: str) -> dict[str, Any]:
    ruta = DIR_CONFIG / nombre
    if not ruta.exists():
        return {}
    return yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}


def config_modelos() -> dict[str, Any]:
    return _yaml("modelos.yaml")


def config_fuentes() -> dict[str, Any]:
    return _yaml("fuentes.yaml")


def config_planes() -> dict[str, Any]:
    return _yaml("planes.yaml")


def huella_incluye_creativo() -> bool:
    return bool((config_fuentes().get("deteccion") or {}).get("huella_incluye_creativo", True))


def tope_gasto_usd() -> float:
    return float(config_modelos().get("tope_gasto_usd_por_corrida", 1.0))


def max_anuncios_analizados() -> int:
    return int(config_modelos().get("max_anuncios_analizados", 25))


def max_anuncios_en_prompt() -> int:
    return int(config_modelos().get("max_anuncios_en_prompt", 45))


def min_anuncios_por_competidor_en_prompt() -> int:
    return int(config_modelos().get("min_anuncios_por_competidor_en_prompt", 6))


def max_anuncios_en_detalle() -> int:
    return int(config_modelos().get("max_anuncios_en_detalle", 12))


def pausa_entre_llamadas() -> float:
    return float(config_modelos().get("pausa_entre_llamadas_seg", 0))
