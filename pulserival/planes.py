"""Los planes que se venden y el estado de la suscripción de cada cliente.

El catálogo (precios, qué incluye, cadencia por defecto) vive en
config/planes.yaml, no acá: cambiar un precio o agregar un plan no debería
necesitar tocar Python. En este módulo solo queda la lógica de "qué
significa" cada plan para el pipeline.

La regla que resuelve la cadencia, en orden:
  1. `clientes.cadencia_dias` — solo existe con periodicidad 'custom' (lo
     exige un CHECK): lo acordado con ESE cliente.
  2. la `cadencia_dias` del plan en config/planes.yaml.
  3. `periodicidad` (semanal|mensual), la verdad para los clientes cargados
     antes de que existieran los planes.
"""
from __future__ import annotations

from typing import Any

from . import config, db, util

# Valores que el código sabe manejar. Van acá y no en un CHECK de SQLite
# porque agregar un plan no debería obligar a reconstruir la tabla, y
# porque el mismo patrón ya se usa en revision.flujo.ETIQUETAS_VALIDAS.
PLANES_VALIDOS = ("autoservicio", "prueba", "mensual", "semanal", "custom")

PERIODICIDADES = ("semanal", "mensual", "custom")

# Cómo se paga el plan de autoservicio. NULL para Custom y para los clientes
# de antes de Paddle.
CICLOS_VALIDOS = ("mensual", "anual")

ESTADOS_VALIDOS = (
    "prueba_pendiente",   # pidió la prueba gratis, todavía no se le generó
    "prueba_usada",       # ya recibió su único reporte de prueba
    "pendiente_pago",     # se registró y todavía no se confirmó el pago
    "activa",             # paga y recibiendo reportes
    "vencida",            # se le cayó el cobro (Paddle está reintentando)
    "pausada",            # la suscripción está en pausa en Paddle
    "cancelada",          # se dio de baja
)

# Con cuáles de esos estados el pipeline le genera reportes. El resto queda
# en la base con su historial, pero no gasta scraper ni IA.
ESTADOS_QUE_REPORTAN = ("prueba_pendiente", "activa")


def catalogo() -> list[dict[str, Any]]:
    return list(config.config_planes().get("planes") or [])


def visibles() -> list[dict[str, Any]]:
    """Los planes que se muestran en la landing. Uno sin la marca se trata
    como visible: así funcionaba el catálogo antes de que existiera."""
    return [p for p in catalogo() if p.get("visible", True)]


def con_checkout() -> list[dict[str, Any]]:
    """Los que se pueden contratar desde el formulario de la web."""
    return [p for p in visibles() if p.get("checkout")]


def ciclo(datos_plan: dict[str, Any], clave: str | None) -> dict[str, Any]:
    """El ciclo de pago de un plan ({} si no existe)."""
    return dict((datos_plan.get("ciclos") or {}).get(clave or "", {}) or {})


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
    """Lo que paga ESTE cliente, por mes.

    El precio acordado le gana al de lista: es justamente lo que define al
    plan a medida. Después cuenta el ciclo: quien paga el año entero paga
    menos por mes, y ese es el número que importa para saber cuánto entra.
    """
    propio = db.valor(cliente, "precio_mensual_usd")
    if propio is not None:
        return float(propio)
    datos = plan(db.valor(cliente, "plan"))
    del_ciclo = ciclo(datos, db.valor(cliente, "ciclo_facturacion"))
    if del_ciclo.get("equivale_mes_usd") is not None:
        return float(del_ciclo["equivale_mes_usd"])
    if del_ciclo.get("precio_usd") is not None:
        return float(del_ciclo["precio_usd"])
    de_lista = datos.get("precio_usd")
    return float(de_lista) if de_lista is not None else None
