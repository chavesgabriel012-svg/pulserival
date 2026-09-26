"""Los planes que se venden y el estado de la suscripción de cada cliente.

El catálogo (precios, qué incluye, cadencia por defecto) vive en
config/planes.yaml, no acá: cambiar un precio o agregar un plan no debería
necesitar tocar Python. En este módulo solo queda la lógica de "qué
significa" cada plan para el pipeline.

La regla que resuelve la cadencia, en orden:
  1. `clientes.cadencia_dias` — lo acordado con ESE cliente (plan a medida).
  2. la `cadencia_dias` del plan en config/planes.yaml.
  3. la columna vieja `periodicidad` (semanal|mensual), que sigue siendo la
     verdad para los clientes cargados antes de que existieran los planes.
"""
from __future__ import annotations

from typing import Any

from . import config, db, util

# Valores que el código sabe manejar. Van acá y no en un CHECK de SQLite
# porque agregar un plan no debería obligar a reconstruir la tabla, y
# porque el mismo patrón ya se usa en revision.flujo.ETIQUETAS_VALIDAS.
PLANES_VALIDOS = ("prueba", "mensual", "semanal", "custom")

ESTADOS_VALIDOS = (
    "prueba_pendiente",   # pidió la prueba gratis, todavía no se le generó
    "prueba_usada",       # ya recibió su único reporte de prueba
    "pendiente_pago",     # se registró y todavía no se confirmó el pago
    "activa",             # paga y recibiendo reportes
    "vencida",            # se le cayó el cobro
    "cancelada",          # se dio de baja
)

# Con cuáles de esos estados el pipeline le genera reportes. El resto queda
# en la base con su historial, pero no gasta scraper ni IA.
ESTADOS_QUE_REPORTAN = ("prueba_pendiente", "activa")


def catalogo() -> list[dict[str, Any]]:
    return list(config.config_planes().get("planes") or [])


def plan(clave: str | None) -> dict[str, Any]:
    """El plan con esa clave, o {} si no existe."""
    for p in catalogo():
        if p.get("clave") == clave:
            return dict(p)
    return {}


def es_recurrente(cliente: Any) -> bool:
    """¿Este cliente recibe reportes de forma continua?

    La prueba gratis no: es un solo reporte. Si el plan no está en el
    catálogo se asume recurrente, que es el comportamiento que tenían todos
    los clientes antes de que existieran los planes.
    """
    datos = plan(db.valor(cliente, "plan"))
    return bool(datos.get("recurrente", True))


def reporta(cliente: Any) -> bool:
    """¿Le toca generar reportes según el estado de su suscripción?

    Un cliente sin estado es uno de antes de los planes: reporta, como
    siempre lo hizo.
    """
    estado = db.valor(cliente, "estado_suscripcion")
    return estado is None or estado in ESTADOS_QUE_REPORTAN


def cadencia_dias(cliente: Any) -> int | None:
    """Cada cuántos días le toca reporte, o None si manda `periodicidad`."""
    propia = db.valor(cliente, "cadencia_dias")
    if propia:
        return int(propia)
    del_plan = plan(db.valor(cliente, "plan")).get("cadencia_dias")
    return int(del_plan) if del_plan else None


def periodo_de(cliente: Any) -> tuple[str, str]:
    """El (inicio, fin) del periodo que le toca a este cliente."""
    periodicidad = db.valor(cliente, "periodicidad", "semanal")
    return util.periodo(periodicidad, dias=cadencia_dias(cliente))


def precio_usd(cliente: Any) -> float | None:
    """Lo que paga ESTE cliente. El precio acordado le gana al de lista:
    es justamente lo que define al plan a medida."""
    propio = db.valor(cliente, "precio_mensual_usd")
    if propio is not None:
        return float(propio)
    de_lista = plan(db.valor(cliente, "plan")).get("precio_usd")
    return float(de_lista) if de_lista is not None else None
