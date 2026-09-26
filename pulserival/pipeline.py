"""Paso 1 del pipeline: RECOLECTAR (y dejar todo listo para reportar).

Una corrida recorre: cliente -> competidor -> plataforma -> fuente,
guarda lo que ve y clasifica qué cambió. Todo queda registrado en
corridas_recoleccion para poder auditar cualquier reporte después.

Se diseñó para ser idempotente: si la corrés dos veces el mismo día no
duplica anuncios ni inventa cambios.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db, planes, priorizar, util
from .fuentes import FuenteError, obtener_fuente

PLATAFORMAS = ("meta", "google")


@dataclass
class Corrida:
    id: int | None = None
    resultados: list[priorizar.ResultadoCompetidor] = field(default_factory=list)
    errores: list[dict[str, str]] = field(default_factory=list)
    saltados: list[dict[str, str]] = field(default_factory=list)
    sospechosas: list[dict[str, str]] = field(default_factory=list)

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
        # Una prueba ya usada, una suscripción vencida o cancelada no va a
        # generar más reportes: raspar sus competidores es gastar Apify para
        # llenar una base que nadie va a leer.
        if not planes.reporta(cliente):
            corrida.saltados.append({
                "competidor": f"(todos los de {cliente['nombre_empresa']})",
                "plataforma": "-",
                "motivo": f"suscripción en estado "
                          f"'{db.valor(cliente, 'estado_suscripcion')}': no se recolecta",
            })
            continue
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
                descartados = getattr(fuente, "descartados", None)
                if descartados:
                    detalle = ", ".join(f"{n} ({c})" for n, c in sorted(descartados.items()))
                    corrida.saltados.append({
                        "competidor": comp["nombre"], "plataforma": plataforma,
                        "motivo": f"se dejaron fuera anuncios de otros anunciantes que pautan "
                                  f"el mismo dominio: {detalle}. Si alguno SÍ es de este "
                                  "competidor, fijá google_anunciante_id en config/clientes.yaml.",
                    })
                res = priorizar.conciliar(con, competidor, plataforma, vistos, corrida.id)
                corrida.resultados.append(res)
                if res.sospechosa:
                    corrida.sospechosas.append({
                        "competidor": comp["nombre"], "plataforma": plataforma,
                        "motivo": "la fuente no devolvió ningún anuncio pero antes había "
                                  "activos: no se marcó nada como pausado. Verificá a mano "
                                  "en la biblioteca pública antes de reportarlo.",
                    })
                if res.nunca_tuvo_datos:
                    corrida.sospechosas.append({
                        "competidor": comp["nombre"], "plataforma": plataforma,
                        "motivo": "nunca devolvió un solo anuncio, en ninguna corrida. Puede "
                                  "ser que no esté pautando, pero es igual de probable que la "
                                  "página o el dominio configurados no sean los suyos. Pasó "
                                  "con Artelec, que tenía ~51 anuncios activos mientras el "
                                  "sistema la reportaba como ausente: verificá a mano antes "
                                  "de darlo por bueno.",
                    })
                con.commit()

    if corrida.errores:
        estado = "parcial" if corrida.resultados else "error"
    elif corrida.sospechosas:
        estado = "parcial"
    else:
        estado = "ok"
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
            "sospechosas": corrida.sospechosas,
        }),
    })
    return corrida


def _toca_reportar(con: sqlite3.Connection, cliente_id: int, periodicidad: str,
                   inicio: str, recurrente: bool = True) -> bool:
    """¿Le toca reporte a este cliente hoy?

    El cron corre todas las semanas, pero un cliente mensual paga un reporte
    por mes: sin este chequeo, una corrida semanal le generaría cuatro
    borradores mensuales al mes, cada uno cobrando IA y solapándose con el
    anterior.

    La regla: se reporta solo si el último reporte de ese cliente cerró antes
    de que arrancara el periodo actual. Para un cliente semanal eso es todas
    las semanas; para uno mensual, una vez cada 30 días.

    Con una excepción: un borrador del periodo ACTUAL que nadie revisó
    todavía se rehace si entró algo nuevo después de escribirlo. No rompe la
    cadencia porque no crea un reporte nuevo, reescribe el mismo, y no gasta
    IA de gusto porque exige que la recolección haya movido algo. Sin esta
    excepción el refresco de más abajo era código muerto: la cadencia cortaba
    antes de llegar, y una corrida que trajo 39 anuncios nuevos, 13 cambiados
    y 6 apagados dejó el borrador tal como estaba.

    `recurrente=False` es la prueba gratis: un solo reporte y se acabó. Sin
    esto, la aritmética de fechas le abriría un periodo nuevo a la semana
    siguiente igual que a cualquier cliente que paga.
    """
    ultimo = db.fila(
        con,
        "SELECT periodo_fin, estado, generado_en FROM reportes_generados "
        "WHERE cliente_id = ? ORDER BY date(periodo_fin) DESC LIMIT 1",
        (cliente_id,),
    )
    if not ultimo or not ultimo["periodo_fin"]:
        return True
    if str(ultimo["periodo_fin"])[:10] < inicio:
        # Le tocaría un periodo nuevo por calendario. El plan sin recurrencia
        # ya gastó el suyo: el borrador que tiene se puede seguir refrescando
        # (abajo), pero no se le abre otro.
        return recurrente
    if ultimo["estado"] != "borrador":
        return False        # ya revisado o enviado: no se toca
    return _hubo_movimiento_despues(con, ultimo["generado_en"])


def _hubo_movimiento_despues(con: sqlite3.Connection, momento: str | None) -> bool:
    """¿Alguna recolección posterior a `momento` movió algo?

    Una corrida donde todos los anuncios siguen igual no cambia el reporte:
    rehacerlo sería pagar IA para reescribir lo mismo.
    """
    if not momento:
        return True
    for fila in db.filas(
        con,
        "SELECT resumen_json FROM corridas_recoleccion "
        "WHERE terminada_en IS NOT NULL AND terminada_en > ?",
        (momento,),
    ):
        try:
            totales = (json.loads(fila["resumen_json"]) or {}).get("totales") or {}
        except (ValueError, TypeError):
            continue
        if any(int(totales.get(k) or 0) for k in ("nuevos", "cambiados", "pausados")):
            return True
    return False


def ciclo_completo(
    con: sqlite3.Connection,
    cliente_id: int | None = None,
    modo: str = "auto",
    exportar: bool = True,
    limite: int = 40,
    solo_reporte: bool = False,
) -> dict[str, Any]:
    """Recolecta y genera los borradores de todos los clientes que toca hoy.

    Esto es lo que corre el cron. NO envía nada: deja los borradores listos
    para tu revisión (Fase 1). En la Fase 3, el envío se agrega acá con una
    condición sobre el control de calidad.
    """
    from .ia import Presupuesto, ProveedorError
    from .reporte import generar as generar_mod
    from .revision import flujo

    if solo_reporte:
        # Regenerar el reporte con lo que ya está en la base, sin volver a
        # llamar al scraper. Los anuncios ya se pagaron una vez.
        corrida = Corrida(id=None)
    else:
        corrida = recolectar(con, cliente_id, modo=modo, limite=limite, disparada_por="cron")
    presupuesto = Presupuesto()
    reportes: list[dict[str, Any]] = []

    for cliente in db.clientes_activos(con, cliente_id):
        periodicidad = db.valor(cliente, "periodicidad", "semanal")
        # La cadencia sale del plan (o de lo acordado con este cliente), no
        # del enum viejo: es lo que hace posible "cada N días".
        inicio, fin = planes.periodo_de(cliente)
        # `solo_reporte` rehace lo que ya está en la base sin llamar al
        # scraper: se permite aunque la suscripción esté cerrada, porque es
        # una acción explícita y no cuesta nada. Lo que no se hace es
        # empezar un periodo nuevo.
        if not solo_reporte and not planes.reporta(cliente):
            reportes.append({
                "cliente": cliente["nombre_empresa"],
                "omitido": f"suscripción en estado "
                           f"'{db.valor(cliente, 'estado_suscripcion')}'",
            })
            continue
        if not solo_reporte and not _toca_reportar(
                con, int(cliente["id"]), periodicidad, inicio,
                recurrente=planes.es_recurrente(cliente)):
            reportes.append({"cliente": cliente["nombre_empresa"],
                             "omitido": "todavía no cierra el periodo de este cliente"})
            continue
        # Un borrador de ESTE mismo periodo que nadie revisó todavía se
        # rehace con lo que acaba de entrar: dejarlo viejo no le sirve a
        # nadie. Uno ya revisado o enviado no se toca. Esto no afecta la
        # cadencia: para un cliente mensual, una semana después el periodo es
        # otro y no coincide.
        existente = db.fila(
            con,
            "SELECT estado FROM reportes_generados WHERE cliente_id = ? "
            "AND periodo_inicio = ? AND periodo_fin = ?",
            (int(cliente["id"]), inicio, fin),
        )
        rehacer = solo_reporte or (existente is not None and existente["estado"] == "borrador")
        try:
            rep = generar_mod.generar(con, cliente, inicio, fin, presupuesto=presupuesto,
                                      regenerar=rehacer)
        except (ProveedorError, RuntimeError) as e:
            reportes.append({"cliente": cliente["nombre_empresa"], "error": str(e)})
            continue
        # La prueba gratis se marca como usada en cuanto tiene su reporte.
        # Es lo que impide que una corrida posterior le abra otro periodo si
        # alguien le cambia las fechas a mano.
        if not planes.es_recurrente(cliente) and not rep.get("ya_existia"):
            if db.valor(cliente, "estado_suscripcion") == "prueba_pendiente":
                db.actualizar(con, "clientes", int(cliente["id"]),
                              {"estado_suscripcion": "prueba_usada"})
        con.commit()
        item = {
            "cliente": cliente["nombre_empresa"],
            "reporte_id": rep["reporte_id"],
            "ya_existia": rep.get("ya_existia", False),
            "validacion": rep.get("validacion", {}),
            "conteo": rep.get("conteo"),
        }
        if exportar and (rehacer or not rep.get("ya_existia")):
            item["archivo"] = str(flujo.exportar(con, rep["reporte_id"]))
            # Además del .md para editar, se deja el correo armado: es la
            # única forma de ver el reporte como lo recibe el cliente sin
            # tener que mandar nada.
            try:
                from .entrega import enviar_reporte

                item["previsualizacion"] = enviar_reporte(
                    con, rep["reporte_id"], simular=True)["archivo"]
            except Exception as e:  # una previa fallida no tumba la corrida
                item["previsualizacion_error"] = str(e)
        reportes.append(item)

    return {
        "corrida_id": corrida.id,
        "totales": corrida.totales(),
        # Cuántas fuentes respondieron. Distinto de los totales: una fuente
        # puede responder correctamente con cero anuncios nuevos.
        "fuentes_ok": len(corrida.resultados),
        "errores": corrida.errores,
        "saltados": corrida.saltados,
        "sospechosas": corrida.sospechosas,
        "reportes": reportes,
        "costo_ia_usd": round(presupuesto.gastado_usd, 5),
    }
