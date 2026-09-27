"""El panel de revisión: el paso humano del producto, en el navegador.

Hasta ahora la revisión se hacía por CLI: `reporte exportar` dejaba un .md,
usted lo editaba en un editor, y `reporte registrar` guardaba su versión con el
diff. Eso funciona y sigue funcionando. El panel es la misma secuencia sin
salir del navegador, y **llama exactamente a las mismas funciones**: el diff
sigue yendo a `ediciones_registradas`, que es el activo que permite ajustar los
prompts con evidencia. Revisar por el panel no pierde nada de lo que se gana
revisando el .md.

Lo que el panel NO hace, a propósito:
  - no genera ni recolecta: eso gasta plata de scraper y de IA, y un botón que
    gasta es un botón que se aprieta sin pensar. Lo dispara el cron.
  - no administra clientes: sigue siendo `cli clientes`.

Sobre el acceso: el panel muestra datos de clientes y puede mandarles correos.
Si no hay contraseña configurada **no se sirve**; no queda abierto. Un panel
abierto en internet con un botón de "enviar" es peor que no tener panel.
"""
from __future__ import annotations

import hmac
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from flask import (Blueprint, Response, abort, current_app, g, redirect,
                   render_template, request, session, url_for)

from .. import config, db, pipeline, util
from ..entrega import EnvioError, enviar_reporte, previsualizar
from ..landing import generador
from ..reporte import datos as datos_mod
from ..reporte import render
from ..revision import flujo

PLANTILLAS = Path(__file__).parent / "plantillas"

panel = Blueprint("panel", __name__, url_prefix="/panel")

# Estados que esperan una decisión suya. 'enviado' y 'descartado' ya se
# decidieron y van en el historial, no en la bandeja.
PENDIENTES = ("borrador", "revisado")
# Intentos de contraseña por ventana, por IP.
MAX_INTENTOS = 8
VENTANA_INTENTOS = 300
_INTENTOS: dict[str, list[float]] = {}


def clave_configurada() -> str | None:
    return config.env("PULSERIVAL_PANEL_CLAVE")


def _contexto() -> dict[str, Any]:
    """Marca y logo, lo mismo que usa la landing."""
    datos = generador.contexto()
    return {"marca": datos.get("marca", "PulseRival"),
            "logo_data_uri": datos.get("logo_data_uri", ""),
            "dominio": datos.get("dominio") or ""}


# ── acceso ───────────────────────────────────────────────────────────
def _token_csrf() -> str:
    """Un token por sesión, para que un formulario ajeno no pueda mandar nada.

    La cookie de sesión va firmada, pero firmada no es lo mismo que a prueba de
    CSRF: sin esto, una página cualquiera podría hacer POST a /enviar con la
    cookie del navegador y mandarle el reporte al cliente. Son quince líneas y
    no hace falta ninguna librería.
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _verificar_csrf() -> None:
    enviado = request.form.get("csrf") or request.headers.get("X-CSRF")
    esperado = session.get("csrf")
    if not esperado or not enviado or not hmac.compare_digest(enviado, esperado):
        abort(400, "La sesión venció o el formulario no viene de acá. Recargue y repita.")


def _demasiados_intentos(ip: str | None) -> bool:
    ahora = time.time()
    marcas = [t for t in _INTENTOS.get(ip or "?", []) if ahora - t < VENTANA_INTENTOS]
    _INTENTOS[ip or "?"] = marcas
    return len(marcas) >= MAX_INTENTOS


@panel.before_request
def _exigir_acceso():
    if not clave_configurada():
        # 503 y no 404: el problema es de configuración y hay que poder
        # distinguirlo de una dirección mal escrita.
        return _mensaje(
            "El panel no está habilitado",
            "Falta la variable PULSERIVAL_PANEL_CLAVE. Sin contraseña el panel no se "
            "sirve: muestra datos de clientes y puede mandarles correos.", 503)
    # Las dos rutas del login, no solo la del GET: con solo `panel.entrar`
    # exento, el POST de la contraseña caía en la comprobación de sesión y
    # redirigía al login otra vez. El panel quedaba inalcanzable.
    if request.endpoint in ("panel.entrar", "panel.entrar_post"):
        return None
    if not session.get("panel"):
        return redirect(url_for("panel.entrar", volver=request.path))
    if request.method == "POST":
        _verificar_csrf()
    return None


@panel.get("/entrar")
def entrar():
    if session.get("panel"):
        return redirect(url_for("panel.bandeja"))
    return render_template("panel_entrar.html.j2", **_contexto(),
                           csrf=_token_csrf(), error=None,
                           volver=request.args.get("volver") or "")


@panel.post("/entrar")
def entrar_post():
    _verificar_csrf()
    ip = request.remote_addr
    if _demasiados_intentos(ip):
        return render_template("panel_entrar.html.j2", **_contexto(),
                               csrf=_token_csrf(), volver="",
                               error="Demasiados intentos. Espere cinco minutos."), 429
    dada = request.form.get("clave") or ""
    # compare_digest y no ==: comparar de largo variable filtra el largo de la
    # contraseña por el tiempo que tarda en fallar.
    if not hmac.compare_digest(dada, clave_configurada() or ""):
        _INTENTOS.setdefault(ip or "?", []).append(time.time())
        return render_template("panel_entrar.html.j2", **_contexto(),
                               csrf=_token_csrf(), volver=request.form.get("volver") or "",
                               error="Contraseña incorrecta."), 401
    session.clear()
    session["panel"] = True
    session.permanent = True
    volver = request.form.get("volver") or ""
    # Solo rutas internas: un `volver` con dominio ajeno convierte el login en
    # un redirector abierto, que es con lo que se arman los enlaces de phishing.
    if volver.startswith("/panel"):
        return redirect(volver)
    return redirect(url_for("panel.bandeja"))


@panel.post("/salir")
def salir():
    session.clear()
    return redirect(url_for("panel.entrar"))


# ── bandeja ──────────────────────────────────────────────────────────
@panel.get("/")
def bandeja():
    con = _conectar()
    try:
        pendientes = db.filas(con, _SQL_BANDEJA.format(
            filtro="r.estado IN ('borrador','revisado')"))
        historial = db.filas(con, _SQL_BANDEJA.format(
            filtro="r.estado IN ('enviado','descartado')") + " LIMIT 25")
    finally:
        con.close()
    return render_template(
        "panel_bandeja.html.j2", **_contexto(), csrf=_token_csrf(),
        pendientes=[_resumen(f) for f in pendientes],
        historial=[_resumen(f) for f in historial])


_SQL_BANDEJA = (
    "SELECT r.*, c.nombre_empresa, c.contacto_email FROM reportes_generados r "
    "JOIN clientes c ON c.id = r.cliente_id WHERE {filtro} "
    "ORDER BY r.generado_en DESC"
)


def _resumen(fila: sqlite3.Row) -> dict[str, Any]:
    """Lo que se ve de un reporte sin abrirlo."""
    val = db.leer_json(fila["validacion_json"], {}) or {}
    datos = db.leer_json(fila["datos_json"], {}) or {}
    conteo = datos.get("conteo") or {}
    return {
        "id": fila["id"],
        "cliente": fila["nombre_empresa"],
        "email": fila["contacto_email"],
        "periodo": f"{fila['periodo_inicio']} al {fila['periodo_fin']}",
        "generado_en": (fila["generado_en"] or "")[:16],
        "estado": fila["estado"],
        "enviado_en": (fila["enviado_en"] or "")[:16],
        "motivo_descarte": db.valor(fila, "motivo_descarte"),
        "asunto": fila["asunto"],
        "aprobado": bool(val.get("aprobado")),
        "problemas": len(val.get("problemas") or []),
        "avisos": len(val.get("avisos") or []),
        "palabras": val.get("palabras"),
        "cobertura": val.get("cobertura_importantes"),
        "movimientos": sum(int(conteo.get(k) or 0)
                           for k in ("nuevo", "cambiado", "pausado")),
        "costo_usd": float(fila["costo_usd"] or 0),
        "editado": bool(fila["final_md"]),
    }


# ── un reporte ───────────────────────────────────────────────────────
@panel.get("/reporte/<int:rid>")
def ver(rid: int):
    con = _conectar()
    try:
        fila = db.fila(con, "SELECT r.*, c.nombre_empresa, c.contacto_email, c.id AS cid "
                            "FROM reportes_generados r JOIN clientes c ON c.id = r.cliente_id "
                            "WHERE r.id = ?", (rid,))
        if not fila:
            abort(404)
        datos = db.leer_json(fila["datos_json"], {}) or {}
        dudosos = pipeline.sospechosas_para_cliente(con, int(fila["cid"]), fila["generado_en"])
    finally:
        con.close()

    cuerpo = fila["final_md"] or fila["borrador_md"] or ""
    return render_template(
        "panel_reporte.html.j2", **_contexto(), csrf=_token_csrf(),
        r=_resumen(fila), cuerpo_md=cuerpo,
        # El borrador original, para poder comparar con lo que ya editó.
        borrador_md=fila["borrador_md"] or "",
        cuerpo_html=render.markdown_a_html(cuerpo),
        validacion=db.leer_json(fila["validacion_json"], {}) or {},
        # `datos_json` guarda la lista plana de anuncios; el agrupado por
        # competidor se deriva. Leerlo con datos.get("por_competidor") mostraba
        # "la corrida no trajo anuncios" en todos los reportes, porque esa clave
        # no está guardada. Se deriva igual que lo hace el armado del correo.
        por_competidor=(datos.get("por_competidor")
                        or datos_mod.agrupar_por_competidor(datos.get("anuncios") or [])),
        conteo=datos.get("conteo") or {},
        dudosos=dudosos,
        etiquetas=flujo.ETIQUETAS_VALIDAS,
        modelo=f"{fila['proveedor_ia'] or '?'}/{fila['modelo_ia'] or '?'}",
        puede_enviar=fila["estado"] == "revisado",
    )


@panel.post("/reporte/<int:rid>/guardar")
def guardar(rid: int):
    cuerpo = request.form.get("cuerpo") or ""
    if not cuerpo.strip():
        return _mensaje("No se guardó",
                        "El reporte quedaría vacío. Si no sirve, descártelo con el motivo.",
                        400, volver=url_for("panel.ver", rid=rid))
    etiqueta = request.form.get("etiqueta") or None
    razon = (request.form.get("razon") or "").strip() or None
    con = _conectar()
    try:
        with con:
            # La misma función que usa el CLI: el diff va al dataset igual.
            # autoetiquetar solo si usted no eligió etiqueta, porque etiquetar
            # con IA cuesta una llamada y su etiqueta es mejor dato.
            res = flujo.registrar_final(con, rid, final_md=cuerpo, etiqueta=etiqueta,
                                        razon=razon, autoetiquetar=not etiqueta)
    except RuntimeError as e:
        return _mensaje("No se pudo guardar", str(e), 409,
                        volver=url_for("panel.ver", rid=rid))
    except ValueError:
        abort(404)
    finally:
        con.close()
    return redirect(url_for("panel.ver", rid=rid,
                            guardado=1, similitud=f"{res['similitud']:.0%}"))


@panel.get("/reporte/<int:rid>/correo")
def correo(rid: int):
    """El correo como le va a llegar al cliente. Se muestra dentro de un marco."""
    con = _conectar()
    try:
        vista = previsualizar(con, rid)
    except EnvioError as e:
        return _mensaje("No se pudo armar el correo", str(e), 400,
                        volver=url_for("panel.ver", rid=rid))
    finally:
        con.close()
    return Response(vista["html"], mimetype="text/html")


@panel.post("/reporte/<int:rid>/enviar")
def enviar(rid: int):
    # El envío le llega a una persona real y no se puede deshacer, así que hay
    # que escribir el nombre del cliente para confirmarlo. Un solo clic de más,
    # y el error que evita es irreversible.
    con = _conectar()
    try:
        fila = db.fila(con, "SELECT c.nombre_empresa FROM reportes_generados r "
                            "JOIN clientes c ON c.id = r.cliente_id WHERE r.id = ?", (rid,))
        if not fila:
            abort(404)
        esperado = (fila["nombre_empresa"] or "").strip().lower()
        if (request.form.get("confirmacion") or "").strip().lower() != esperado:
            return _mensaje(
                "No se envió",
                f"Para confirmar el envío hay que escribir el nombre del cliente tal cual: "
                f"«{fila['nombre_empresa']}». No se mandó nada.", 400,
                volver=url_for("panel.ver", rid=rid))
        simular = request.form.get("simular") == "1"
        try:
            with con:
                res = enviar_reporte(con, rid, simular=simular)
        except EnvioError as e:
            return _mensaje("No se envió", str(e), 409,
                            volver=url_for("panel.ver", rid=rid))
    finally:
        con.close()
    if res.get("enviado"):
        return _mensaje("Enviado",
                        f"El reporte salió a {res['para']} por {res['canal']}.",
                        volver=url_for("panel.bandeja"))
    return _mensaje(
        "Simulado, no enviado",
        f"No se mandó nada. Quedó la copia de lo que se habría enviado a {res['para']} "
        f"en {res['archivo']}.", volver=url_for("panel.ver", rid=rid))


@panel.post("/reporte/<int:rid>/descartar")
def descartar(rid: int):
    motivo = (request.form.get("motivo") or "").strip()
    con = _conectar()
    try:
        with con:
            flujo.descartar(con, rid, motivo)
    except ValueError as e:
        # `descartar` usa ValueError para "no existe" y para "falta el motivo".
        if "motivo" in str(e):
            return _mensaje("No se descartó", str(e), 400,
                            volver=url_for("panel.ver", rid=rid))
        abort(404)
    except RuntimeError as e:
        return _mensaje("No se descartó", str(e), 409,
                        volver=url_for("panel.ver", rid=rid))
    finally:
        con.close()
    return redirect(url_for("panel.bandeja"))


# ── gasto ────────────────────────────────────────────────────────────
@panel.get("/gasto")
def gasto():
    con = _conectar()
    try:
        ia = db.filas(con,
            "SELECT tarea, proveedor, modelo, COUNT(*) AS llamadas, "
            "SUM(tokens_entrada) AS entrada, SUM(tokens_salida) AS salida, "
            "round(SUM(costo_usd), 4) AS costo_usd, SUM(1-exito) AS fallos "
            "FROM uso_ia GROUP BY tarea, proveedor, modelo ORDER BY SUM(costo_usd) DESC")
        total_ia = db.fila(con, "SELECT round(SUM(costo_usd), 4) AS t FROM uso_ia")
        # Apify se cobra por anuncio traído, y eso queda en la corrida.
        corridas = db.filas(con,
            "SELECT id, substr(iniciada_en, 1, 16) AS cuando, estado, fuente, "
            "disparada_por, round(costo_usd, 4) AS costo_usd "
            "FROM corridas_recoleccion ORDER BY iniciada_en DESC LIMIT 15")
        total_fuentes = db.fila(
            con, "SELECT round(SUM(costo_usd), 4) AS t FROM corridas_recoleccion")
        por_cliente = db.filas(con,
            "SELECT c.nombre_empresa AS cliente, COUNT(r.id) AS reportes, "
            "round(SUM(r.costo_usd), 4) AS costo_usd "
            "FROM reportes_generados r JOIN clientes c ON c.id = r.cliente_id "
            "GROUP BY c.id ORDER BY SUM(r.costo_usd) DESC")
    finally:
        con.close()
    ti = float((total_ia or {})["t"] or 0) if total_ia else 0.0
    tf = float((total_fuentes or {})["t"] or 0) if total_fuentes else 0.0
    return render_template(
        "panel_gasto.html.j2", **_contexto(), csrf=_token_csrf(),
        ia=[dict(f) for f in ia], corridas=[dict(f) for f in corridas],
        por_cliente=[dict(f) for f in por_cliente],
        total_ia=ti, total_fuentes=tf, total=ti + tf,
        tope=config.tope_gasto_usd())


# ── utilidades ───────────────────────────────────────────────────────
def _conectar() -> sqlite3.Connection:
    return current_app.config["CONECTAR"]()


def _mensaje(titulo: str, mensaje: str, codigo: int = 200,
             volver: str | None = None):
    html = render_template("panel_mensaje.html.j2", **_contexto(),
                           titulo_pagina=titulo, mensaje=mensaje, volver=volver)
    return (html, codigo) if codigo != 200 else html
