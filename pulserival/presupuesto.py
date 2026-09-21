"""Cuánto va a costar al mes, antes de gastarlo.

El costo real de PulseRival no es la IA (son centavos): es el scraper, que
cobra por anuncio devuelto. Este módulo proyecta ese costo a partir de los
clientes y competidores que ya tenés cargados, para que sepas si el plan
gratuito de Apify te alcanza o si hay que pasar al de pago.

Los precios salen de config/fuentes.yaml, sección `costos`.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import config, db

CORRIDAS_POR_MES = {"semanal": 4.33, "mensual": 1.0}


def _costos() -> dict[str, Any]:
    return (config.config_fuentes().get("costos") or {})


def proyectar(con: sqlite3.Connection, limite_por_competidor: int = 40) -> dict[str, Any]:
    """Proyección mensual del gasto en scrapers, cliente por cliente.

    Es el peor caso: asume que cada competidor devuelve el límite completo de
    anuncios en cada corrida. En la práctica suele ser menos, porque pocos
    negocios ticos tienen 40 anuncios activos a la vez.
    """
    costos = _costos()
    plan = costos.get("plan_apify", "free")
    tarifas = costos.get("usd_por_mil_resultados") or {}
    credito = float((costos.get("credito_mensual_usd") or {}).get(plan, 0.0))

    detalle: list[dict[str, Any]] = []
    total = 0.0
    for cliente in db.clientes_activos(con):
        corridas = CORRIDAS_POR_MES.get(cliente["periodicidad"] or "semanal", 4.33)
        por_cliente = {"cliente": cliente["nombre_empresa"],
                       "periodicidad": cliente["periodicidad"],
                       "competidores": 0, "anuncios_mes": 0, "costo_usd": 0.0}
        for competidor in db.competidores_de(con, int(cliente["id"])):
            comp = dict(competidor)
            por_cliente["competidores"] += 1
            for plataforma, tiene in (
                ("meta", comp.get("meta_pagina_url") or comp.get("meta_consulta") or comp.get("meta_pagina_id")),
                ("google", comp.get("google_dominio") or comp.get("google_anunciante") or comp.get("google_anunciante_id")),
            ):
                if not tiene:
                    continue
                anuncios = limite_por_competidor * corridas
                tarifa = float((tarifas.get(plataforma) or {}).get(plan, 0.0))
                costo = anuncios / 1000 * tarifa
                por_cliente["anuncios_mes"] += round(anuncios)
                por_cliente["costo_usd"] += costo
        por_cliente["costo_usd"] = round(por_cliente["costo_usd"], 2)
        total += por_cliente["costo_usd"]
        detalle.append(por_cliente)

    gasto_real = db.fila(con, "SELECT round(SUM(costo_usd), 4) AS t FROM uso_ia")
    return {
        "plan_apify": plan,
        "credito_mensual_usd": credito,
        "limite_por_competidor": limite_por_competidor,
        "clientes": detalle,
        "scrapers_usd_mes": round(total, 2),
        "credito_restante_usd": round(credito - total, 2),
        "alcanza_el_credito": total <= credito,
        "ia_gastada_hasta_hoy_usd": (gasto_real["t"] if gasto_real and gasto_real["t"] else 0.0),
        "clientes_que_caben_en_el_credito": (
            int(credito / (total / len(detalle))) if detalle and total > 0 else None
        ),
    }


def formatear(p: dict[str, Any]) -> str:
    L = [f"Plan de Apify: {p['plan_apify']} · crédito incluido ${p['credito_mensual_usd']:.2f}/mes",
         f"Supuesto: hasta {p['limite_por_competidor']} anuncios por competidor por corrida (peor caso)",
         ""]
    if not p["clientes"]:
        L.append("  Todavía no hay clientes cargados: el gasto proyectado es $0.")
    for c in p["clientes"]:
        L.append(f"  {c['cliente']} ({c['periodicidad']}, {c['competidores']} competidores): "
                 f"~{c['anuncios_mes']} anuncios/mes · ${c['costo_usd']:.2f}/mes")
    L.append("")
    L.append(f"  Scrapers: ${p['scrapers_usd_mes']:.2f}/mes")
    if p["alcanza_el_credito"]:
        L.append(f"  ✓ Entra en el crédito del plan. Te sobran ${p['credito_restante_usd']:.2f}/mes.")
    else:
        L.append(f"  ! Te pasás del crédito por ${abs(p['credito_restante_usd']):.2f}/mes: "
                 "subí de plan o bajá --limite.")
    if p["clientes_que_caben_en_el_credito"]:
        L.append(f"  Con este plan te caben ~{p['clientes_que_caben_en_el_credito']} cliente(s) "
                 "de este tamaño.")
    L.append(f"  IA gastada hasta hoy (real, no proyección): ${p['ia_gastada_hasta_hoy_usd']}")
    return "\n".join(L)
