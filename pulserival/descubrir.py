"""Deducir los competidores de un cliente que no indicó ninguno.

Para pedir el reporte alcanzan el nombre de la empresa y un correo. Si el
cliente no dice quién es su competencia, la buscamos: se consultan en la
Biblioteca de Anuncios de Meta (Costa Rica) los términos de su industria
(config/industrias.yaml) y se toman los anunciantes con más anuncios activos,
menos el propio cliente.

Por qué los que más anuncian y no otros: son los que se pueden reportar. Un
competidor que no pauta no aparece en ninguna biblioteca, y deducirlo sería
inventarlo. Y por qué Meta y no Google: la Biblioteca de Meta busca por
palabra clave y dice quién pautó cada anuncio; el Centro de Transparencia de
Google busca por anunciante o dominio, que es justo lo que no sabemos.

Los competidores deducidos quedan guardados como cualquier otro, con una nota
que lo dice. El reporte pasa por revisión humana antes de enviarse: quien lo
revisa ve a quién se eligió y puede cambiarlo con `competidores editar`.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

from . import config, db, planes
from .fuentes import FuenteError, obtener_fuente

# Cuántos competidores se deducen. Los mismos que admite el plan de $25.
MAXIMO = 4
# Cuántos anuncios se piden por término. Se paga por anuncio.
LIMITE_POR_TERMINO = 40
NOTA = "Deducido por PulseRival a partir de su industria (búsqueda: {termino})."

# Palabras que no distinguen a una empresa de otra: "Ferretería El Roble" y
# "El Roble S.A." son la misma por "roble", no por "el" ni por "sa".
_VACIAS = {"el", "la", "los", "las", "de", "del", "y", "e", "sa", "s", "a", "srl", "ltda",
           "cr", "costa", "rica", "the", "and", "inc", "corp", "grupo", "tienda"}


def industrias() -> list[dict[str, Any]]:
    return list(config.config_industrias().get("industrias") or [])


def nombres_de_industrias() -> list[str]:
    return [i["nombre"] for i in industrias() if i.get("nombre")]


def terminos(cliente: Any) -> list[str]:
    """Qué buscar para este cliente: los de su industria, o del nombre."""
    industria = (db.valor(cliente, "industria") or "").strip().lower()
    for i in industrias():
        if (i.get("nombre") or "").strip().lower() == industria and i.get("busqueda"):
            return [t for t in i["busqueda"] if t]
    # Sin industria conocida: la primera palabra del nombre de la empresa
    # suele ser el rubro ("Ferretería El Roble", "Clínica Santa Ana").
    nombre = (db.valor(cliente, "nombre_empresa") or "").split()
    return [nombre[0]] if nombre and len(nombre[0]) > 3 else []


def _palabras(texto: str) -> set[str]:
    plano = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return {p for p in re.findall(r"[a-z0-9]+", plano.lower()) if p not in _VACIAS and len(p) > 1}


def es_el_mismo(cliente_nombre: str, anunciante: str, generico: set[str] | None = None) -> bool:
    """¿Este anunciante es el propio cliente? Comparten una palabra distintiva.

    `generico` son las palabras del rubro ("ferretería"): que dos ferreterías
    digan "ferretería" no las hace la misma.
    """
    propias = _palabras(cliente_nombre) - (generico or set())
    return bool(propias & _palabras(anunciante))


def deducir(con: sqlite3.Connection, cliente: Any, modo: str = "auto",
            maximo: int = MAXIMO) -> dict[str, Any]:
    """Busca, elige y guarda los competidores. Devuelve qué hizo.

    No hace commit: lo hace quien llama, igual que el resto del pipeline.
    """
    cid = int(cliente["id"])
    lista = terminos(cliente)
    if not lista:
        return {"cliente": cliente["nombre_empresa"], "agregados": [],
                "motivo": "no tiene industria ni un nombre del que sacar qué buscar"}

    fuente = obtener_fuente("meta", modo)
    generico = set().union(*(_palabras(t) for t in lista))
    conteo: dict[str, dict[str, Any]] = {}
    errores = []
    for termino in lista:
        try:
            anuncios = fuente.buscar(termino, limite=LIMITE_POR_TERMINO)
        except FuenteError as e:
            errores.append(f"{termino}: {e}")
            continue
        for a in anuncios:
            nombre = (a.anunciante or "").strip()
            if not nombre or es_el_mismo(cliente["nombre_empresa"], nombre, generico):
                continue
            clave = str((a.metadata or {}).get("anunciante_id") or nombre.lower())
            fila = conteo.setdefault(clave, {"nombre": nombre, "id": (a.metadata or {})
                                             .get("anunciante_id"), "anuncios": 0,
                                             "termino": termino})
            fila["anuncios"] += 1

    elegidos = sorted(conteo.values(), key=lambda f: -f["anuncios"])[:maximo]
    agregados = []
    for orden, e in enumerate(elegidos, start=1):
        db.insertar(con, "competidores_seguidos", {
            "cliente_id": cid,
            "nombre": e["nombre"],
            "clave": re.sub(r"[^a-z0-9]+", "-", e["nombre"].lower()).strip("-")[:40] or None,
            # El id de página es lo más confiable para volver a pedir sus
            # anuncios; sin id, se lo busca por nombre.
            "meta_pagina_id": str(e["id"]) if e["id"] else None,
            "meta_consulta": None if e["id"] else e["nombre"],
            # En Google se lo busca por nombre de anunciante.
            "google_anunciante": e["nombre"],
            "prioridad": 1 if orden == 1 else 2,
            "notas": NOTA.format(termino=e["termino"]),
        })
        agregados.append(e["nombre"])
    resultado = {"cliente": cliente["nombre_empresa"], "agregados": agregados}
    if not agregados:
        resultado["motivo"] = ("la búsqueda no encontró anunciantes"
                               + (f" ({'; '.join(errores)})" if errores else ""))
    return resultado


def completar(con: sqlite3.Connection, cliente_id: int | None = None,
              modo: str = "auto") -> list[dict[str, Any]]:
    """Deduce competidores para todos los clientes que reportan y no tienen."""
    hechos = []
    for cliente in db.clientes_activos(con, cliente_id):
        if not planes.reporta(cliente) or db.competidores_de(con, int(cliente["id"])):
            continue
        hechos.append(deducir(con, cliente, modo=modo))
        con.commit()
    return hechos
