"""Clientes y competidores, desde el panel.

El reporte sale aunque el cliente no indique competidores: el ciclo los
deduce de su industria (pulserival/descubrir.py). Eso deja tres casos que
tiene que resolver una persona, y para eso está esta parte del panel:

  - un cliente sin competidores: la búsqueda no encontró a nadie (industria
    "Otra", un nombre que no dice el rubro, Apify caído);
  - competidores que eligió el sistema y nadie revisó;
  - un competidor del que nunca llegó un anuncio: puede que no paute, o que
    su nombre o su página estén mal.

Desde acá se agregan, corrigen y quitan competidores, y se puede volver a
buscar, recolectar o rehacer un reporte sin abrir la terminal. Lo que gasta
plata (Apify, IA) pide una casilla de confirmación que dice cuánto: el
panel ya no evita esos botones, pero no deja que se aprieten sin leer.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from flask import abort, current_app, redirect, render_template, request, url_for

from .. import altas, config, db, descubrir, pipeline, planes
from .panel import _conectar, _contexto, _mensaje, _token_csrf, panel

PREFIJO_DEDUCIDO = descubrir.NOTA.split("{")[0].split("(")[0].strip()


# ── qué necesita atención ────────────────────────────────────────────
def _conteo_de_anuncios(con: sqlite3.Connection) -> dict[int, dict[str, int]]:
    salida: dict[int, dict[str, int]] = {}
    for f in db.filas(con, "SELECT competidor_id, plataforma, COUNT(*) AS n, "
                           "SUM(estado = 'activo') AS activos FROM anuncios_detectados "
                           "GROUP BY competidor_id, plataforma"):
        d = salida.setdefault(int(f["competidor_id"]), {"total": 0, "activos": 0})
        d[f["plataforma"]] = int(f["n"])
        d["total"] += int(f["n"])
        d["activos"] += int(f["activos"] or 0)
    return salida


def _ultima_corrida_terminada(con: sqlite3.Connection) -> str | None:
    fila = db.fila(con, "SELECT MAX(terminada_en) AS t FROM corridas_recoleccion "
                        "WHERE terminada_en IS NOT NULL")
    return fila["t"] if fila else None


def competidores_con_estado(con: sqlite3.Connection, cliente_id: int,
                            conteo: dict | None = None,
                            ultima: str | None = None) -> list[dict[str, Any]]:
    """Los competidores activos del cliente, con lo que hay que mirar de cada uno."""
    conteo = _conteo_de_anuncios(con) if conteo is None else conteo
    ultima = _ultima_corrida_terminada(con) if ultima is None else ultima
    salida = []
    for c in db.competidores_de(con, cliente_id):
        d = dict(c)
        n = conteo.get(int(d["id"]), {"total": 0, "activos": 0})
        d["anuncios"] = n
        d["deducido"] = (d.get("notas") or "").startswith(PREFIJO_DEDUCIDO)
        # "Sin anuncios" solo si ya hubo una corrida después de cargarlo: uno
        # recién agregado todavía no tuvo oportunidad.
        d["sin_anuncios"] = (n["total"] == 0 and bool(ultima)
                             and str(ultima) >= str(d.get("creado_en") or ""))
        salida.append(d)
    return salida


def resumen_de_cliente(con: sqlite3.Connection, cliente: Any, conteo: dict,
                       ultima: str | None) -> dict[str, Any]:
    cid = int(cliente["id"])
    comps = competidores_con_estado(con, cid, conteo, ultima)
    ultimo = db.fila(con, "SELECT id, estado, periodo_fin, generado_en FROM reportes_generados "
                          "WHERE cliente_id = ? ORDER BY generado_en DESC LIMIT 1", (cid,))
    reporta = planes.reporta(cliente)
    problemas = []
    if reporta and not comps:
        problemas.append("Sin competidores: la búsqueda automática no encontró a nadie, "
                         "o todavía no corrió el ciclo.")
    deducidos = [c for c in comps if c["deducido"]]
    if deducidos:
        n = len(deducidos)
        problemas.append(f"{n} competidor{'es' if n != 1 else ''} elegido{'s' if n != 1 else ''} "
                         "por el sistema: revisar que de verdad compitan.")
    sin = [c for c in comps if c["sin_anuncios"]]
    if sin:
        problemas.append(f"Sin anuncios encontrados: {', '.join(c['nombre'] for c in sin)}. "
                         "Puede que no paute, o que el nombre o la página estén mal.")
    return {
        "id": cid,
        "empresa": cliente["nombre_empresa"],
        "email": cliente["contacto_email"],
        "industria": db.valor(cliente, "industria"),
        "plan": (planes.plan(db.valor(cliente, "plan")) or {}).get("nombre") or db.valor(cliente, "plan"),
        "estado": db.valor(cliente, "estado_suscripcion"),
        "reporta": reporta,
        "competidores": comps,
        "ultimo_reporte": dict(ultimo) if ultimo else None,
        "problemas": problemas,
    }


def atencion(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Los clientes que reportan y tienen algo que resolver."""
    conteo = _conteo_de_anuncios(con)
    ultima = _ultima_corrida_terminada(con)
    salida = []
    for c in db.clientes_activos(con):
        r = resumen_de_cliente(con, c, conteo, ultima)
        if r["reporta"] and r["problemas"]:
            salida.append(r)
    return salida


# ── páginas ──────────────────────────────────────────────────────────
@panel.get("/clientes")
def clientes():
    con = _conectar()
    try:
        conteo = _conteo_de_anuncios(con)
        ultima = _ultima_corrida_terminada(con)
        todos = [resumen_de_cliente(con, c, conteo, ultima)
                 for c in db.filas(con, "SELECT * FROM clientes WHERE activo = 1 ORDER BY id DESC")]
    finally:
        con.close()
    # Lo que pide atención, arriba.
    todos.sort(key=lambda r: (not (r["reporta"] and r["problemas"]), -r["id"]))
    return render_template("panel_clientes.html.j2", **_contexto(), csrf=_token_csrf(),
                           clientes=todos)


def _cliente_o_404(con: sqlite3.Connection, cid: int) -> sqlite3.Row:
    fila = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (cid,))
    if not fila:
        abort(404)
    return fila


def _tareas():
    return current_app.config["TAREAS"]


@panel.get("/cliente/<int:cid>")
def cliente(cid: int):
    con = _conectar()
    try:
        fila = _cliente_o_404(con, cid)
        resumen = resumen_de_cliente(con, fila, _conteo_de_anuncios(con),
                                     _ultima_corrida_terminada(con))
        pendiente = db.fila(con, "SELECT id, estado FROM reportes_generados WHERE cliente_id = ? "
                                 "AND estado IN ('borrador','revisado') "
                                 "ORDER BY generado_en DESC LIMIT 1", (cid,))
    finally:
        con.close()
    return render_template(
        "panel_cliente.html.j2", **_contexto(), csrf=_token_csrf(),
        c=resumen, notas=db.valor(fila, "notas") or "",
        industrias=descubrir.nombres_de_industrias(),
        terminos=descubrir.terminos(fila),
        pendiente=dict(pendiente) if pendiente else None,
        tarea=_tareas().estado(cid), ocupado=_tareas().ocupado())


@panel.post("/cliente/<int:cid>/datos")
def cliente_datos(cid: int):
    """Correo, industria y contexto: lo que más cambia la calidad del reporte
    y de la búsqueda de competidores."""
    con = _conectar()
    try:
        _cliente_o_404(con, cid)
        cambios: dict[str, Any] = {
            "industria": (request.form.get("industria") or "").strip() or None,
            "notas": (request.form.get("notas") or "").strip() or None,
        }
        email = (request.form.get("email") or "").strip()
        if email:
            try:
                cambios["contacto_email"] = altas.validar_email(email)
            except altas.AltaInvalida as e:
                return _mensaje("No se guardó", str(e), 400,
                                volver=url_for("panel.cliente", cid=cid))
        with con:
            db.actualizar(con, "clientes", cid, cambios)
    finally:
        con.close()
    return redirect(url_for("panel.cliente", cid=cid))


def _competidor_del_formulario() -> dict[str, Any]:
    return altas.normalizar_competidor({
        "nombre": request.form.get("nombre"),
        "meta_pagina_url": request.form.get("facebook"),
        "google_dominio": request.form.get("web"),
        "prioridad": request.form.get("prioridad") or 2,
    })


@panel.post("/cliente/<int:cid>/competidores")
def competidor_agregar(cid: int):
    con = _conectar()
    try:
        _cliente_o_404(con, cid)
        try:
            datos = _competidor_del_formulario()
        except altas.AltaInvalida as e:
            return _mensaje("No se agregó", str(e), 400, volver=url_for("panel.cliente", cid=cid))
        previo = db.fila(con, "SELECT id, activo FROM competidores_seguidos "
                              "WHERE cliente_id = ? AND lower(nombre) = lower(?)",
                         (cid, datos["nombre"]))
        if previo and previo["activo"]:
            return _mensaje("No se agregó", "Ese competidor ya está cargado para este cliente.",
                            409, volver=url_for("panel.cliente", cid=cid))
        with con:
            if previo:
                # Se había quitado: vuelve con sus anuncios de antes, que son
                # los que permiten decir "esto es nuevo".
                db.actualizar(con, "competidores_seguidos", int(previo["id"]), {
                    **datos, "activo": 1, "meta_pagina_id": None,
                    "google_anunciante_id": None, "notas": "Agregado desde el panel."})
            else:
                claves = {f["clave"] for f in db.filas(
                    con, "SELECT clave FROM competidores_seguidos WHERE cliente_id = ?",
                    (cid,)) if f["clave"]}
                db.insertar(con, "competidores_seguidos", {
                    "cliente_id": cid, "clave": altas.clave_desde(datos["nombre"], claves),
                    **datos, "notas": "Agregado desde el panel."})
    finally:
        con.close()
    return redirect(url_for("panel.cliente", cid=cid))


def _competidor_o_404(con: sqlite3.Connection, comp_id: int) -> sqlite3.Row:
    fila = db.fila(con, "SELECT * FROM competidores_seguidos WHERE id = ?", (comp_id,))
    if not fila:
        abort(404)
    return fila


@panel.post("/competidor/<int:comp_id>/editar")
def competidor_editar(comp_id: int):
    con = _conectar()
    try:
        fila = _competidor_o_404(con, comp_id)
        try:
            datos = _competidor_del_formulario()
        except altas.AltaInvalida as e:
            return _mensaje("No se guardó", str(e), 400,
                            volver=url_for("panel.cliente", cid=fila["cliente_id"]))
        deducido = (fila["notas"] or "").startswith(PREFIJO_DEDUCIDO)
        igual = (datos["nombre"] == fila["nombre"]
                 and datos["meta_pagina_url"] == fila["meta_pagina_url"]
                 and datos["google_dominio"] == fila["google_dominio"])
        if igual:
            # Guardar sin cambiar nada es confirmarlo: se conservan los ids
            # con que se lo encontró y deja de figurar como "por revisar".
            cambios: dict[str, Any] = {"prioridad": datos["prioridad"]}
            if deducido:
                cambios["notas"] = "Elegido por el sistema y confirmado en el panel."
        else:
            # Cambió cómo se lo busca: los ids viejos ya no corresponden y se
            # borran, para que la próxima corrida use lo nuevo.
            cambios = {**datos, "meta_pagina_id": None, "google_anunciante_id": None}
            if deducido:
                cambios["notas"] = "Elegido por el sistema y corregido en el panel."
        with con:
            db.actualizar(con, "competidores_seguidos", comp_id, cambios)
    finally:
        con.close()
    return redirect(url_for("panel.cliente", cid=fila["cliente_id"]))


@panel.post("/competidor/<int:comp_id>/quitar")
def competidor_quitar(comp_id: int):
    """Se desactiva, no se borra: sus anuncios son el historial."""
    con = _conectar()
    try:
        fila = _competidor_o_404(con, comp_id)
        with con:
            db.actualizar(con, "competidores_seguidos", comp_id, {"activo": 0})
    finally:
        con.close()
    return redirect(url_for("panel.cliente", cid=fila["cliente_id"]))


# ── lo que corre en segundo plano ────────────────────────────────────
def _modo() -> str:
    return config.env("PULSERIVAL_CICLO_MODO") or "auto"


def _limite() -> int:
    try:
        return int(config.env("PULSERIVAL_CICLO_LIMITE") or 40)
    except ValueError:
        return 40


def _lanzar(cid: int, tipo: str, trabajo) -> Any:
    if request.form.get("confirmo") != "si":
        return _mensaje("No se hizo nada",
                        "Hace falta marcar la casilla de confirmación: esto consume Apify o IA.",
                        400, volver=url_for("panel.cliente", cid=cid))
    error = _tareas().lanzar(cid, tipo, trabajo)
    if error:
        return _mensaje("No se hizo nada", error, 409, volver=url_for("panel.cliente", cid=cid))
    return redirect(url_for("panel.cliente", cid=cid))


@panel.post("/cliente/<int:cid>/buscar")
def cliente_buscar(cid: int):
    con = _conectar()
    try:
        _cliente_o_404(con, cid)
    finally:
        con.close()

    def trabajo(c: sqlite3.Connection) -> dict[str, Any]:
        fila = _cliente_o_404(c, cid)
        return descubrir.deducir(c, fila, modo=_modo())

    return _lanzar(cid, "buscar", trabajo)


@panel.post("/cliente/<int:cid>/generar")
def cliente_generar(cid: int):
    def trabajo(c: sqlite3.Connection) -> dict[str, Any]:
        from .. import mantenimiento

        try:
            mantenimiento.respaldar(conservar=7)
        except Exception:  # sin respaldo igual se genera, como en el ciclo
            pass
        return pipeline.ciclo_completo(c, cliente_id=cid, modo=_modo(), limite=_limite())

    return _lanzar(cid, "generar", trabajo)


@panel.post("/cliente/<int:cid>/rehacer")
def cliente_rehacer(cid: int):
    def trabajo(c: sqlite3.Connection) -> dict[str, Any]:
        return pipeline.ciclo_completo(c, cliente_id=cid, modo=_modo(), solo_reporte=True)

    return _lanzar(cid, "rehacer", trabajo)
