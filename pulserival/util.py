"""Utilidades chicas y sin dependencias."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

ESPACIOS = re.compile(r"\s+")


def ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def hoy() -> date:
    return date.today()


def normalizar_texto(texto: str | None) -> str:
    """Minúsculas, sin acentos, sin espacios repetidos. Para comparar textos."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return ESPACIOS.sub(" ", texto.lower()).strip()


def huella(*partes: Any) -> str:
    """Hash estable del contenido de un anuncio.

    Si el hash cambia, el anuncio cambió (texto, oferta, destino o creativo).
    Solo se normaliza el texto: así un cambio de mayúsculas no cuenta como
    anuncio nuevo, pero cambiar la oferta sí.
    """
    crudo = "|".join(normalizar_texto(p) for p in partes)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]


def periodo(periodicidad: str, referencia: date | None = None) -> tuple[str, str]:
    """Devuelve (inicio, fin) del periodo que se está reportando.

    Semanal  -> los 7 días anteriores a hoy.
    Mensual  -> los 30 días anteriores a hoy.
    """
    fin = referencia or hoy()
    dias = 7 if periodicidad == "semanal" else 30
    return (fin - timedelta(days=dias)).isoformat(), fin.isoformat()


def recortar(texto: str | None, largo: int = 180) -> str:
    texto = (texto or "").strip().replace("\n", " ")
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


def buscar_anidado(datos: Any, ruta: str) -> Any:
    """Lee 'a.b.0.c' dentro de dicts y listas. Devuelve None si no existe."""
    actual = datos
    for tramo in ruta.split("."):
        if actual is None:
            return None
        if isinstance(actual, dict):
            actual = actual.get(tramo)
        elif isinstance(actual, list):
            if not tramo.isdigit() or int(tramo) >= len(actual):
                return None
            actual = actual[int(tramo)]
        else:
            return None
    return actual


def primer_valor(datos: dict, rutas: list[str]) -> Any:
    """Devuelve el primer campo con valor entre varios nombres posibles."""
    for ruta in rutas or []:
        valor = buscar_anidado(datos, ruta)
        if valor not in (None, "", [], {}):
            return valor
    return None


def contar_palabras(texto: str | None) -> int:
    return len((texto or "").split())
