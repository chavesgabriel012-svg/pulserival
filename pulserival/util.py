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


# Días que cubre cada periodicidad, contando ambos extremos.
DIAS_PERIODO = {"semanal": 7, "mensual": 30}


def periodo(periodicidad: str, referencia: date | None = None,
            dias: int | None = None) -> tuple[str, str]:
    """Devuelve (inicio, fin) del periodo que se está reportando, ambos incluidos.

    Semanal  -> 7 días: de hoy-6 a hoy.
    Mensual  -> 30 días: de hoy-29 a hoy.

    `dias` le gana a `periodicidad`: es lo que permite una cadencia
    arbitraria (cada 10 días, cada 45) sin tener que meter valores nuevos en
    el CHECK de la columna `periodicidad`, que SQLite no sabe modificar sin
    reconstruir la tabla entera.

    El -1 importa: las comparaciones de fecha del reporte incluyen los dos
    extremos, así que con `hoy - 7` el día del borde caía dentro de dos
    periodos seguidos y el mismo anuncio se reportaba dos veces como nuevo.
    """
    fin = referencia or hoy()
    if dias is None:
        dias = DIAS_PERIODO.get(periodicidad, 30)
    dias = max(1, int(dias))
    return (fin - timedelta(days=dias - 1)).isoformat(), fin.isoformat()


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


# Rangos Unicode de emojis, pictogramas, banderas y símbolos decorativos.
EMOJIS = re.compile(
    "[" 
    "\U0001F300-\U0001FAFF"   # pictogramas, emoticones, objetos
    "\U00002600-\U000027BF"   # símbolos varios y dingbats
    "\U0001F1E6-\U0001F1FF"   # banderas
    "\U00002190-\U000021FF"   # flechas
    "\U0000FE00-\U0000FE0F"   # selectores de variación
    "\U00002B00-\U00002BFF"
    "\U0000200D"              # unión de emojis
    "]+",
    flags=re.UNICODE,
)


def sin_emojis(texto: str | None) -> str | None:
    """Quita emojis y limpia los espacios que dejan.

    Los anuncios vienen llenos de emojis. En el reporte quedan mal: es un
    documento de trabajo, no una publicación de redes. Se limpian acá, en la
    capa de datos, para que no puedan llegar al cliente por ninguna vía: ni
    citados del anuncio, ni copiados por el modelo.
    """
    if not texto:
        return texto
    limpio = EMOJIS.sub("", str(texto))
    limpio = re.sub(r"[ \t]{2,}", " ", limpio)
    limpio = re.sub(r"\n{3,}", "\n\n", limpio)
    return "\n".join(linea.strip() for linea in limpio.splitlines()).strip()


def contar_palabras(texto: str | None) -> int:
    return len((texto or "").split())
