"""Fuente Apify: llama un scraper ya construido y programable por API.

Por qué Apify y no un scraper propio:
  - La Biblioteca de Anuncios de Meta y el Centro de Transparencia de Google
    NO tienen API oficial para anuncios comerciales de Costa Rica.
  - Mantener un scraper propio significa arreglarlo cada vez que cambia el
    HTML. Ese es trabajo de ingeniería continuo que no querés tener.
  - Apify cobra por resultado (centavos por reporte) y ya mantiene el scraper.

El mapeo de campos vive en config/fuentes.yaml, no acá: si el actor cambia un
nombre de campo, se arregla en el YAML.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from .. import config, util
from .base import AnuncioCrudo, FuenteError

BASE = "https://api.apify.com/v2"
ESPERA_MAX_SEG = 600


class FuenteApify:
    def __init__(self, plataforma: str, token: str | None = None):
        self.plataforma = plataforma
        self.cfg = config.config_fuentes().get(plataforma) or {}
        if not self.cfg:
            raise FuenteError(f"No hay configuración para la plataforma '{plataforma}' en config/fuentes.yaml")
        self.actor = self.cfg.get("actor")
        self.token = token or config.env("APIFY_TOKEN")
        if not self.token:
            raise FuenteError(
                "Falta APIFY_TOKEN en .env. Sacalo en "
                "https://console.apify.com/account/integrations"
            )
        self.nombre = f"apify:{self.actor}"

    # ── entrada que se le manda al actor ─────────────────────────────
    def construir_entrada(self, competidor: dict, limite: int) -> dict[str, Any]:
        entrada = dict(self.cfg.get("entrada_base") or {})
        if self.plataforma == "meta":
            entrada["resultsLimit"] = limite
            urls = []
            pagina = competidor.get("meta_pagina_url")
            if pagina:
                urls.append({"url": pagina.rstrip("/") + "/", "method": "GET"})
            consulta = competidor.get("meta_consulta") or competidor.get("nombre")
            if not urls and consulta:
                plantilla = self.cfg.get("plantilla_url_busqueda", "")
                urls.append({"url": plantilla.format(consulta=requests.utils.quote(consulta)), "method": "GET"})
            if not urls:
                raise FuenteError(
                    f"El competidor '{competidor.get('nombre')}' no tiene página de Meta "
                    "ni término de búsqueda. Agregá uno con: competidores editar"
                )
            entrada["startUrls"] = urls
        elif self.plataforma == "google":
            entrada["maxAdsPerQuery"] = limite
            consulta = (
                competidor.get("google_anunciante_id")
                or competidor.get("google_dominio")
                or competidor.get("google_anunciante")
                or competidor.get("nombre")
            )
            entrada["queries"] = [consulta]
        return entrada

    # ── llamada al actor ─────────────────────────────────────────────
    def _correr_actor(self, entrada: dict[str, Any]) -> list[dict]:
        ruta = self.actor.replace("/", "~")
        url = f"{BASE}/acts/{ruta}/run-sync-get-dataset-items"
        try:
            r = requests.post(
                url,
                params={"token": self.token, "timeout": ESPERA_MAX_SEG},
                json=entrada,
                timeout=ESPERA_MAX_SEG + 30,
            )
        except requests.RequestException as e:
            raise FuenteError(f"No se pudo llamar al actor {self.actor}: {e}") from e
        if r.status_code == 402:
            raise FuenteError("Apify rechazó la corrida por límite de crédito (402).")
        if r.status_code == 401:
            raise FuenteError("APIFY_TOKEN inválido (401).")
        if r.status_code >= 400:
            raise FuenteError(f"Apify respondió {r.status_code}: {util.recortar(r.text, 300)}")
        try:
            datos = r.json()
        except ValueError as e:
            raise FuenteError(f"Apify devolvió algo que no es JSON: {util.recortar(r.text, 200)}") from e
        return datos if isinstance(datos, list) else [datos]

    def traer(self, competidor: dict, limite: int = 40) -> list[AnuncioCrudo]:
        entrada = self.construir_entrada(competidor, limite)
        crudos = self._correr_actor(entrada)
        self._guardar_crudo(competidor, crudos)
        return [a for a in (self.mapear(item) for item in crudos) if a and not a.vacio()]

    # ── traducción a AnuncioCrudo usando el mapeo del YAML ───────────
    def mapear(self, item: dict) -> AnuncioCrudo | None:
        if not isinstance(item, dict):
            return None
        m = self.cfg.get("mapeo") or {}
        def campo(nombre: str):
            return util.primer_valor(item, m.get(nombre, []))

        plataformas = campo("plataformas")
        id_externo = _texto(campo("id_externo"))
        return AnuncioCrudo(
            plataforma=self.plataforma,
            fuente=self.nombre,
            id_externo=id_externo,
            anunciante=_texto(campo("anunciante")),
            titulo=_texto(campo("titulo")),
            texto=_texto(campo("texto")),
            descripcion=_texto(campo("descripcion")),
            cta=_texto(campo("cta")),
            link_destino=_texto(campo("link_destino")),
            creativo_url=_texto(campo("creativo_url")) or _miniatura_youtube(campo("video_id")),
            tipo_creativo=_tipo_creativo(item, campo("formato"), campo("creativo_url")),
            url_anuncio=_texto(campo("url_anuncio")) or self._url_ficha(id_externo),
            fecha_inicio=_fecha(campo("fecha_inicio")),
            fecha_fin=_fecha(campo("fecha_fin")),
            metadata={
                "plataformas_publicacion": plataformas,
                "anunciante_id": _texto(campo("anunciante_id")),
                "categoria_anunciante": campo("categoria"),
                "activo_en_la_fuente": campo("activo"),
                # Google no trae el texto, pero sí estas señales, que son lo
                # único con lo que el reporte puede hablar de esa plataforma.
                "dias_al_aire": campo("dias_al_aire"),
                "variaciones": campo("variaciones"),
                "regiones": campo("regiones"),
                "video_url": _texto(campo("video_url")),
                "crudo_claves": sorted(item)[:40],
            },
        )

    def _url_ficha(self, id_externo: str | None) -> str | None:
        """Link a la ficha pública del anuncio.

        El actor de Meta no lo devuelve, pero el reporte lo necesita: es lo
        que le permite al cliente abrir el anuncio y comprobar lo que le
        estamos contando. Se arma con el id de archivo.
        """
        plantilla = self.cfg.get("plantilla_url_anuncio")
        if not plantilla or not id_externo:
            return None
        return plantilla.format(id=id_externo)

    def _guardar_crudo(self, competidor: dict, crudos: list[dict]) -> None:
        """Guarda la respuesta cruda en datos/crudo/ para poder auditar después."""
        destino = config.DIR_DATOS / "crudo"
        destino.mkdir(parents=True, exist_ok=True)
        marca = time.strftime("%Y%m%d-%H%M%S")
        archivo = destino / f"{self.plataforma}-{competidor.get('id','x')}-{marca}.json"
        archivo.write_text(json.dumps(crudos, ensure_ascii=False, indent=2), encoding="utf-8")


def _texto(valor: Any) -> str | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, (list, tuple)):
        valor = next((v for v in valor if v), None)
    if isinstance(valor, dict):
        valor = valor.get("text") or valor.get("value") or json.dumps(valor, ensure_ascii=False)
    return str(valor).strip() or None


FORMATOS = {
    "IMAGE": "imagen", "VIDEO": "video", "DCO": "dinamico", "DPA": "catalogo",
    "CAROUSEL": "carrusel", "TEXT": "texto", "MULTI_IMAGES": "carrusel",
}


def _tipo_creativo(item: dict, formato, creativo_url: str | None) -> str:
    """El formato declarado por la fuente manda; si no viene, se deduce."""
    if isinstance(formato, str) and formato.upper() in FORMATOS:
        return FORMATOS[formato.upper()]
    # Ojo: no alcanza con buscar la palabra "video" en el JSON. Varios
    # actores mandan youtubeVideoId/videoUrl en null para TODOS los anuncios,
    # y así se clasificaba como video hasta un anuncio de texto.
    for clave in ("youtubeVideoId", "youtubeUrl", "videoUrl", "videoHdUrl"):
        if item.get(clave):
            return "video"
    if any((v or {}).get("videoHdUrl") or (v or {}).get("videoSdUrl")
           for v in (util.buscar_anidado(item, "snapshot.videos") or [])):
        return "video"
    if creativo_url:
        return "imagen"
    return "texto"


def _miniatura_youtube(video_id: Any) -> str | None:
    """Miniatura de un anuncio de video de YouTube.

    Los anuncios de video del centro de transparencia no traen imagen de
    vista previa, solo el id del video. Sin esto, el anexo del reporte
    mostraría una fila sin nada que mirar.
    """
    vid = _texto(video_id)
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None


def _fecha(valor: Any) -> str | None:
    """Normaliza la fecha a AAAA-MM-DD.

    Los actores mezclan formatos: unos mandan ISO ("2026-06-29T07:00:00.000Z")
    y otros una marca de tiempo Unix (1782716400), que en el reporte se leería
    como un número suelto sin sentido para el cliente.
    """
    if valor in (None, "", []):
        return None
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
        from datetime import datetime, timezone

        try:
            return datetime.fromtimestamp(int(valor), tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    texto = _texto(valor)
    return texto.split("T")[0] if texto else None
