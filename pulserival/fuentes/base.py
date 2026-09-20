"""Contrato común de las fuentes.

Cada plataforma (Meta, Google) tiene campos distintos. Acá definimos el
"idioma común" al que todas se traducen: AnuncioCrudo. El resto del sistema
solo conoce AnuncioCrudo, así que agregar TikTok o LinkedIn mañana es escribir
una fuente nueva sin tocar nada más.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .. import util


class FuenteError(RuntimeError):
    """La fuente no pudo traer datos (sin token, actor caído, etc.)."""


@dataclass
class AnuncioCrudo:
    plataforma: str                      # 'meta' | 'google'
    fuente: str                          # de dónde salió, para trazabilidad
    id_externo: str | None = None
    anunciante: str | None = None
    titulo: str | None = None
    texto: str | None = None
    descripcion: str | None = None
    cta: str | None = None
    link_destino: str | None = None
    creativo_url: str | None = None
    tipo_creativo: str | None = None
    url_anuncio: str | None = None
    fecha_inicio: str | None = None
    fecha_fin: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def huella(self) -> str:
        """Identidad del contenido: si esto cambia, el anuncio cambió."""
        return util.huella(
            self.titulo, self.texto, self.descripcion, self.cta,
            _dominio(self.link_destino), self.creativo_url,
        )

    def vacio(self) -> bool:
        return not any([self.titulo, self.texto, self.descripcion, self.creativo_url])


def _dominio(url: str | None) -> str:
    """Solo el dominio del link: los parámetros de campaña (utm_*) cambian
    todo el tiempo y no significan un anuncio nuevo."""
    if not url:
        return ""
    sin_esquema = url.split("://", 1)[-1]
    return sin_esquema.split("/", 1)[0].lower()


class Fuente(Protocol):
    nombre: str

    def traer(self, competidor: dict, limite: int) -> list[AnuncioCrudo]:
        """Devuelve los anuncios que la fuente ve hoy para ese competidor."""
        ...


def obtener_fuente(plataforma: str, modo: str = "auto"):
    """Fábrica de fuentes.

    modo='demo'  -> datos de ejemplo, sin internet ni costo (para probar).
    modo='auto'  -> scraper real si hay APIFY_TOKEN; si no, demo con aviso.
    modo='apify' -> exige el scraper real (falla si no hay token).
    """
    from . import apify, demo, meta_api

    if plataforma == "meta_api_oficial":
        return meta_api.FuenteMetaApiOficial()
    if modo == "demo":
        return demo.FuenteDemo(plataforma)
    if modo == "apify":
        return apify.FuenteApify(plataforma)
    # auto
    from .. import config
    if config.env("APIFY_TOKEN"):
        return apify.FuenteApify(plataforma)
    return demo.FuenteDemo(plataforma, aviso=True)
