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
        from .. import config

        partes = [self.titulo, self.texto, self.descripcion, self.cta,
                  _destino(self.link_destino)]
        if config.huella_incluye_creativo():
            partes.append(_creativo(self.creativo_url))
        return util.huella(*partes)

    def vacio(self) -> bool:
        return not any([self.titulo, self.texto, self.descripcion, self.creativo_url])


def _destino(url: str | None) -> str:
    """Dominio + ruta del link, sin los parámetros.

    Los parámetros de campaña (utm_*, fbclid) cambian todo el tiempo y no
    significan un anuncio nuevo, así que se descartan. La ruta sí se conserva:
    mandar el mismo texto a /promo-setiembre o a /black-friday es un cambio de
    oferta, y el cliente quiere saberlo.
    """
    if not url:
        return ""
    sin_esquema = url.split("://", 1)[-1]
    sin_params = sin_esquema.split("?", 1)[0].split("#", 1)[0]
    return sin_params.rstrip("/").lower()


def _creativo(url: str | None) -> str:
    """Identidad estable de la imagen o el video del anuncio.

    La URL completa NO sirve: Meta firma sus enlaces de CDN en cada consulta
    y además rota entre decenas de servidores (scontent-lax3-1, scontent-
    iad3-1, y así). Medido sobre una corrida real: 68 de 77 anuncios
    aparecían como "cambiados" teniendo el texto idéntico, solo porque la
    firma y el servidor eran otros. Un reporte que cada semana le anuncia al
    cliente 77 cambios que no ocurrieron deja de ser creíble a la segunda
    semana.

    Lo estable es el nombre del archivo, que identifica la pieza:
        scontent-lax3-1.xx.fbcdn.net/v/t39.../795697003_1373407654959818_n.jpg?oh=...
        -> 795697003_1373407654959818_n.jpg
    """
    if not url:
        return ""
    sin_parametros = str(url).split("?", 1)[0].split("#", 1)[0]
    return sin_parametros.rstrip("/").rsplit("/", 1)[-1].lower()


class Fuente(Protocol):
    nombre: str

    def traer(self, competidor: dict, limite: int) -> list[AnuncioCrudo]:
        """Devuelve los anuncios que la fuente ve hoy para ese competidor."""
        ...


def obtener_fuente(plataforma: str, modo: str = "auto"):
    """Fábrica de fuentes.

    modo='demo'  -> datos de ejemplo, sin internet ni costo (para probar).
    modo='auto'  -> el scraper real; falla con un mensaje claro si falta el token.
    modo='apify' -> igual que auto, explícito.

    Por qué 'auto' NO cae a los datos de ejemplo: los datos de demo son un
    gimnasio inventado. Si el token se vence o alguien lo borra del servidor,
    caer a demo en silencio pondría anuncios ficticios en el reporte de un
    cliente que paga. Es mejor que la corrida falle y te avise: un reporte
    tarde se explica, un reporte inventado te cuesta el cliente.
    """
    from . import apify, demo, meta_api

    if plataforma == "meta_api_oficial":
        return meta_api.FuenteMetaApiOficial()
    if modo == "demo":
        return demo.FuenteDemo(plataforma)
    if modo == "apify":
        return apify.FuenteApify(plataforma)
    # auto: igual que apify. FuenteApify ya explica dónde sacar el token si falta.
    return apify.FuenteApify(plataforma)
