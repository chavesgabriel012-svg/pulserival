"""Paso 1 del pipeline: RECOLECTAR (y dejar todo listo para reportar).

Una corrida recorre: cliente -> competidor -> plataforma -> fuente,
guarda lo que ve y clasifica qué cambió. Todo queda registrado en
corridas_recoleccion para poder auditar cualquier reporte después.

Se diseñó para ser idempotente: si la corrés dos veces el mismo día no
duplica anuncios ni inventa cambios.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db, priorizar, util
from .fuentes import FuenteError, obtener_fuente

PLATAFORMAS = ("meta", "google")


@dataclass
class Corrida:
    id: int | None = None
    resultados: list[priorizar.ResultadoCompetidor] = field(default_factory=list)
    errores: list[dict[str, str]] = field(default_factory=list)
    saltados: list[dict[str, str]] = field(default_factory=list)

    def totales(self) -> dict[str, int]:
        t = {"nuevos": 0, "cambiados": 0, "continuan": 0, "pausados": 0}
        for r in self.resultados:
            for k, v in r.resumen().items():
                t[k] += v
        return t


def _tiene_datos_para(competidor: dict, plataforma: str) -> bool:
    if plataforma == "meta":
        return bool(competidor.get("meta_pagina_url") or competidor.get("meta_consulta")
                    or competidor.get("meta_pagina_id"))
    return bool(competidor.get("google_dominio") or competidor.get("google_anunciante")
                or competidor.get("google_anunciante_id"))


def recolectar(
    con: sqlite3.Connection,
    cliente_id: int | None = None,
    modo: str = "auto",
    plataformas: tuple[str, ...] = PLATAFORMAS,
    limite: int = 40,
    disparada_por: str = "manual",
) -> Corrida:
    corrida = Corrida()
    corrida.id = db.insertar(con, "corridas_recoleccion", {
        "fuente": modo, "disparada_por": disparada_por, "estado": "en_curso",
    })
    con.commit()

    for cliente in db.clientes_activos(con, cliente_id):
        for competidor in db.competidores_de(con, int(cliente["id"])):
            comp = dict(competidor)
            for plataforma in plataformas:
                if not _tiene_datos_para(comp, plataforma):
                    corrida.saltados.append({
                        "competidor": comp["nombre"], "plataforma": plataforma,
                        "motivo": "el competidor no tiene datos configurados para esa plataforma",
                    })
                    continue
                try:
                    fuente = obtener_fuente(plataforma, modo)
                    vistos = fuente.traer(comp, limite=limite)
                except FuenteError as e:
                    corrida.errores.append({
                        "competidor": comp["nombre"], "plataforma": plataforma, "error": str(e),
                    })
                    continue
                res = priorizar.conciliar(con, competidor, plataforma, vistos, corrida.id)
                corrida.resultados.append(res)
                con.commit()

    estado = "ok" if not corrida.errores else ("parcial" if corrida.resultados else "error")
    db.actualizar(con, "corridas_recoleccion", corrida.id, {
        "terminada_en": util.ahora_iso(),
        "estado": estado,
        "resumen_json": db.json_o_nada({
            "totales": corrida.totales(),
            "por_competidor": [
                {"competidor": r.competidor, "plataforma": r.plataforma, **r.resumen()}
                for r in corrida.resultados
            ],
            "errores": corrida.errores,
            "saltados": corrida.saltados,
        }),
    })
    return corrida


def ciclo_completo(
    con: sqlite3.Connection,
    cliente_id: int | None = None,
    modo: str = "auto",
    exportar: bool = True,
) -> dict[str, Any]:
    """Recolecta y genera los borradores de todos los clientes que toca hoy.

    Esto es lo que corre el cron. NO envía nada: deja los borradores listos
    para tu revisión (Fase 1). En la Fase 3, el envío se agrega acá con una
    condición sobre el control de calidad.
    """
    from .ia import Presupuesto, ProveedorError
    from .reporte import generar as generar_mod
    from .revision import flujo

    corrida = recolectar(con, cliente_id, modo=modo, disparada_por="cron")
    presupuesto = Presupuesto()
    reportes: list[dict[str, Any]] = []

    for cliente in db.clientes_activos(con, cliente_id):
        inicio, fin = util.periodo(cliente["periodicidad"] or "semanal")
        try:
            rep = generar_mod.generar(con, cliente, inicio, fin, presupuesto=presupuesto)
        except (ProveedorError, RuntimeError) as e:
            reportes.append({"cliente": cliente["nombre_empresa"], "error": str(e)})
            continue
        con.commit()
        item = {
            "cliente": cliente["nombre_empresa"],
            "reporte_id": rep["reporte_id"],
            "ya_existia": rep.get("ya_existia", False),
            "validacion": rep.get("validacion", {}),
            "conteo": rep.get("conteo"),
        }
        if exportar and not rep.get("ya_existia"):
            item["archivo"] = str(flujo.exportar(con, rep["reporte_id"]))
        reportes.append(item)

    return {
        "corrida_id": corrida.id,
        "totales": corrida.totales(),
        "errores": corrida.errores,
        "saltados": corrida.saltados,
        "reportes": reportes,
        "costo_ia_usd": round(presupuesto.gastado_usd, 5),
    }
