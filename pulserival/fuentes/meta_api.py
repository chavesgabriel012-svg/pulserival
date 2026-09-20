"""Fuente COMPLEMENTARIA: API oficial de Meta Ad Library (endpoint ads_archive).

LEER ANTES DE USAR
──────────────────
Esta API NO devuelve anuncios comerciales normales dirigidos a Costa Rica.
Por decisión de plataforma, solo entrega:
  1) anuncios de temas políticos o sociales, en cualquier país; y
  2) cualquier anuncio (comercial incluido) que se haya entregado en la Unión
     Europea o Reino Unido, por obligación del DSA.
Un token verificado y activo no cambia eso: es una restricción de la
plataforma, no un problema de permisos.

Entonces, ¿para qué está acá? Para dos casos concretos:
  - un cliente cuyos competidores pautan también en UE/UK;
  - un cliente que necesita anuncios políticos o de interés social (partidos,
    cámaras, ONGs, campañas de temas sociales).
Para el caso principal —comercios ticos pautando en CR— la fuente válida es
el scraper de la interfaz web pública (fuentes/apify.py).
"""
from __future__ import annotations

import json
from typing import Any

import requests

from .. import config, util
from .base import AnuncioCrudo, FuenteError

BASE = "https://graph.facebook.com"


class FuenteMetaApiOficial:
    nombre = "meta_api_oficial"
    plataforma = "meta"

    def __init__(self, token: str | None = None):
        self.cfg = config.config_fuentes().get("meta_api_oficial") or {}
        self.token = token or config.env("META_AD_LIBRARY_TOKEN")
        self.version = self.cfg.get("version_api", "v21.0")

    def traer(self, competidor: dict, limite: int = 40, paises: list[str] | None = None) -> list[AnuncioCrudo]:
        if not self.token:
            raise FuenteError(
                "Falta META_AD_LIBRARY_TOKEN. Paso a paso en docs/03-meta-api-paso-a-paso.md"
            )
        consulta = competidor.get("meta_consulta") or competidor.get("nombre")
        params = {
            "access_token": self.token,
            "search_terms": consulta,
            "ad_reached_countries": json.dumps(paises or self.cfg.get("paises_por_defecto") or ["CR"]),
            "ad_type": "ALL",
            "limit": min(limite, 100),
            "fields": ",".join(self.cfg.get("campos") or ["id", "ad_creative_bodies"]),
        }
        try:
            r = requests.get(f"{BASE}/{self.version}/ads_archive", params=params, timeout=60)
        except requests.RequestException as e:
            raise FuenteError(f"No se pudo consultar la Graph API: {e}") from e
        cuerpo = r.json() if r.content else {}
        if r.status_code >= 400:
            error = (cuerpo.get("error") or {}).get("message", r.text)
            raise FuenteError(f"Graph API respondió {r.status_code}: {util.recortar(error, 300)}")
        datos = cuerpo.get("data") or []
        return [a for a in (self._mapear(x) for x in datos) if not a.vacio()]

    def _mapear(self, item: dict[str, Any]) -> AnuncioCrudo:
        return AnuncioCrudo(
            plataforma="meta",
            fuente="meta_api_oficial",
            id_externo=item.get("id"),
            anunciante=item.get("page_name"),
            titulo=_primero(item.get("ad_creative_link_titles")),
            texto=_primero(item.get("ad_creative_bodies")),
            descripcion=_primero(item.get("ad_creative_link_descriptions")),
            link_destino=_primero(item.get("ad_creative_link_captions")),
            url_anuncio=item.get("ad_snapshot_url"),
            fecha_inicio=item.get("ad_delivery_start_time"),
            fecha_fin=item.get("ad_delivery_stop_time"),
            tipo_creativo="texto",
            metadata={
                "plataformas_publicacion": item.get("publisher_platforms"),
                "idiomas": item.get("languages"),
                "anunciante_id": item.get("page_id"),
                "nota_fuente": "API oficial: solo político/social o entregado en UE/UK",
            },
        )


def _primero(valor: Any) -> str | None:
    if isinstance(valor, list):
        return next((str(v) for v in valor if v), None)
    return str(valor) if valor else None
