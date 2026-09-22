"""Paso 2 del pipeline: PRIORIZAR.

Recibe lo que la fuente vio hoy y lo compara con lo que ya teníamos guardado.
Devuelve la clasificación que después usa el reporte:

  nuevo    -> no lo habíamos visto nunca (huella nueva, id nuevo)
  cambiado -> mismo anuncio (mismo id externo) con contenido distinto:
              cambió la oferta, el texto, el destino o el creativo
  continua -> igual que la corrida anterior; sigue corriendo
  pausado  -> lo veníamos viendo y hoy la fuente ya no lo devuelve

Esta es la parte que convierte "una lista de anuncios" en "qué pasó esta
semana", que es lo que el cliente realmente paga.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db, util
from .fuentes.base import AnuncioCrudo


@dataclass
class ResultadoCompetidor:
    competidor_id: int
    competidor: str
    plataforma: str
    sospechosa: bool = False   # la fuente no devolvió nada y antes sí había
    nunca_tuvo_datos: bool = False  # jamás devolvió un anuncio: revisar la config
    nuevos: list[int] = field(default_factory=list)
    cambiados: list[int] = field(default_factory=list)
    continuan: list[int] = field(default_factory=list)
    pausados: list[int] = field(default_factory=list)

    def resumen(self) -> dict[str, int]:
        return {
            "nuevos": len(self.nuevos),
            "cambiados": len(self.cambiados),
            "continuan": len(self.continuan),
            "pausados": len(self.pausados),
        }


def conciliar(
    con: sqlite3.Connection,
    competidor: sqlite3.Row | dict,
    plataforma: str,
    vistos: list[AnuncioCrudo],
    corrida_id: int | None = None,
) -> ResultadoCompetidor:
    """Guarda los anuncios vistos y los clasifica contra lo ya conocido."""
    competidor = dict(competidor)
    cid = int(competidor["id"])
    res = ResultadoCompetidor(cid, competidor.get("nombre", "?"), plataforma)
    ahora = util.ahora_iso()

    previos = db.filas(
        con,
        "SELECT * FROM anuncios_detectados WHERE competidor_id = ? AND plataforma = ?",
        (cid, plataforma),
    )
    activos_previos = [f for f in previos if f["estado"] == "activo"]

    if not vistos and activos_previos:
        # La fuente respondió bien pero sin un solo anuncio, y la semana pasada
        # había varios activos. Puede ser real (el competidor apagó todo), pero
        # es mucho más probable que sea el scraper fallando en silencio: un
        # cambio de HTML, un bloqueo, una búsqueda que dejó de coincidir.
        # Marcar todo como "pausado" produciría un reporte falso y alarmista,
        # así que no se toca nada y la corrida queda señalada para que la mires.
        res.sospechosa = True
        return res

    if not vistos and not previos:
        # Nunca devolvió un solo anuncio, en ninguna corrida. Eso NO prueba
        # que la empresa no pautara: es igual de probable que la página o el
        # dominio configurados no sean los suyos. Pasó con Artelec, que tenía
        # ~51 anuncios activos en Meta mientras el sistema la reportaba como
        # ausente, y el reporte llegó a recomendar aprovechar ese hueco.
        res.nunca_tuvo_datos = True
        return res

    reemplazados: set[int] = set()   # versiones viejas de anuncios que cambiaron
    huellas_vistas: set[str] = set()
    por_huella = {f["huella"]: f for f in previos}
    por_externo: dict[str, list[sqlite3.Row]] = {}
    for f in previos:
        if f["id_externo"]:
            por_externo.setdefault(f["id_externo"], []).append(f)

    for crudo in vistos:
        h = crudo.huella()
        if h in huellas_vistas:
            # El scraper devolvió el mismo anuncio dos veces en la misma
            # corrida (pasa seguido). Ya lo procesamos: se ignora.
            continue
        huellas_vistas.add(h)
        existente = por_huella.get(h)

        if existente:
            # Ya lo teníamos idéntico: solo actualizamos "visto por última vez".
            db.actualizar(con, "anuncios_detectados", existente["id"],
                          {"visto_ultimo_en": ahora, "estado": "activo"})
            hermanos_activos = [
                f for f in por_externo.get(crudo.id_externo or "", [])
                if f["estado"] == "activo" and int(f["id"]) != int(existente["id"])
            ]
            if existente["estado"] == "pausado" and hermanos_activos:
                # El competidor volvió a una versión anterior del anuncio
                # (A -> B -> A). Es un cambio de contenido, no un anuncio que
                # se cayó: se apaga la versión B y se cuenta como cambiado.
                for hermano in hermanos_activos:
                    db.actualizar(con, "anuncios_detectados", hermano["id"],
                                  {"estado": "pausado", "visto_ultimo_en": ahora})
                    reemplazados.add(int(hermano["id"]))
                res.cambiados.append(int(existente["id"]))
            else:
                res.continuan.append(int(existente["id"]))
            continue

        # Huella nueva. ¿Es un anuncio conocido que cambió, o uno nuevo?
        hermanos = por_externo.get(crudo.id_externo or "", [])
        fila = _guardar(con, cid, crudo, corrida_id, ahora)
        if hermanos:
            # El mismo anuncio de la plataforma con contenido distinto.
            for viejo in hermanos:
                db.actualizar(con, "anuncios_detectados", viejo["id"],
                              {"estado": "pausado", "visto_ultimo_en": ahora})
                reemplazados.add(int(viejo["id"]))
            res.cambiados.append(fila)
        else:
            res.nuevos.append(fila)

    # Lo que veníamos viendo activo y hoy no apareció: se pausó.
    for f in previos:
        if int(f["id"]) in reemplazados:
            continue   # no es una pausa: es la versión anterior de un anuncio que cambió
        if f["huella"] not in huellas_vistas and f["estado"] == "activo":
            db.actualizar(con, "anuncios_detectados", f["id"], {"estado": "pausado"})
            res.pausados.append(int(f["id"]))

    return res


def _guardar(
    con: sqlite3.Connection,
    competidor_id: int,
    crudo: AnuncioCrudo,
    corrida_id: int | None,
    ahora: str,
) -> int:
    datos: dict[str, Any] = {
        "competidor_id": competidor_id,
        "plataforma": crudo.plataforma,
        "fuente": crudo.fuente,
        "id_externo": crudo.id_externo,
        "huella": crudo.huella(),
        "anunciante": crudo.anunciante,
        "titulo": crudo.titulo,
        "texto": crudo.texto,
        "descripcion": crudo.descripcion,
        "cta": crudo.cta,
        "link_destino": crudo.link_destino,
        "creativo_url": crudo.creativo_url,
        "tipo_creativo": crudo.tipo_creativo,
        "url_anuncio": crudo.url_anuncio,
        "fecha_inicio": crudo.fecha_inicio,
        "fecha_fin": crudo.fecha_fin,
        "visto_primero_en": ahora,
        "visto_ultimo_en": ahora,
        "estado": "activo",
        "metadata_json": db.json_o_nada(crudo.metadata),
        "corrida_id": corrida_id,
    }
    return db.insertar(con, "anuncios_detectados", datos)
