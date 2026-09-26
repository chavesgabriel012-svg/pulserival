"""Servidor web de PulseRival.

Sirve tres cosas y nada más:
  1. la landing, con los planes que salen de config/planes.yaml;
  2. el alta: el formulario que antes había que cargar a mano con
     `cli clientes agregar`;
  3. el cobro, HOY SIMULADO. No hay pasarela conectada: el checkout es una
     pantalla que marca la suscripción como pagada sin mover plata. Está
     marcado como simulación en la interfaz para que nadie lo confunda.

Deliberadamente NO incluye el panel de administración: eso sigue operándose
por CLI hasta que haga falta.

Sobre dónde queda el alta: depende de PULSERIVAL_DEPOSITO, y eso decide qué
hosting sirve. Con `sqlite` (por defecto) escribe en la base que apunte
`PULSERIVAL_DB`, y el proceso necesita un disco que persista entre reinicios.
Con `github` escribe un YAML en el repositorio y no toca ninguna base, que es
lo que permite correr en un hosting serverless como Vercel. El detalle está en
`pulserival/web/deposito.py`.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from flask import Flask, abort, redirect, render_template, request, url_for

from .. import altas, config, db, planes
from ..landing import generador
from . import deposito as deposito_mod

# El cobro real todavía no existe. Mientras esta bandera esté encendida, el
# checkout es una simulación y lo dice en pantalla.
def cobro_simulado() -> bool:
    return (config.env("PULSERIVAL_COBRO") or "simulado").lower() != "real"


def crear_app(ruta_db: str | None = None, deposito=None) -> Flask:
    # Las plantillas son las mismas que usa la landing estática: una sola
    # copia del HTML para los dos modos.
    app = Flask(__name__, template_folder=str(generador.PLANTILLAS))
    app.config["RUTA_DB"] = ruta_db or str(config.ruta_db())

    def conectar() -> sqlite3.Connection:
        con = sqlite3.connect(app.config["RUTA_DB"])
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    deposito = deposito or deposito_mod.obtener(conectar)
    app.config["DEPOSITO"] = deposito

    # ── landing ──────────────────────────────────────────────────────
    @app.get("/")
    def inicio():
        ctx = generador.contexto()
        ctx["modo_servidor"] = True
        ctx["accion_alta"] = url_for("alta")
        return render_template("index.html.j2", **ctx)

    # ── alta ─────────────────────────────────────────────────────────
    @app.post("/alta")
    def alta():
        if _demasiadas_peticiones(request.remote_addr):
            return _pagina(
                "Demasiadas solicitudes",
                "Recibimos varias solicitudes desde esta conexión en poco tiempo. "
                "Espere un minuto y vuelva a intentar.", volver=True), 429

        datos = request.form
        competidores = []
        for n in range(1, 9):
            nombre = (datos.get(f"comp{n}") or "").strip()
            if not nombre:
                continue
            competidores.append({
                "nombre": nombre,
                "meta_pagina_url": datos.get(f"fb{n}"),
                "google_dominio": datos.get(f"web{n}"),
                "prioridad": 1 if n == 1 else 2,
            })

        try:
            resultado = deposito.guardar({
                "empresa": datos.get("empresa", ""),
                "email": datos.get("email", ""),
                "competidores": competidores,
                "plan": datos.get("plan") or "semanal",
                "contacto": datos.get("contacto"),
                "whatsapp": datos.get("whatsapp"),
                "industria": datos.get("industria"),
                "notas": datos.get("contexto"),
            })
        except altas.AltaInvalida as e:
            return _pagina("No pudimos completar el registro", str(e), volver=True), 400
        except deposito_mod.DepositoError as e:
            # Un fallo del depósito no es culpa de quien llenó el formulario, y
            # perder el alta en silencio es lo peor que puede pasar acá: se le
            # dice que escriba, con los datos que ya cargó a la vista.
            app.logger.error("No se pudo depositar el alta: %s", e)
            return _pagina(
                "No pudimos guardar su solicitud",
                "Hubo un problema de nuestro lado, no con los datos que cargó. "
                "Escríbanos y la registramos a mano: "
                + (_contacto_visible() or "el contacto está al pie de la página") + ".",
                volver=True), 502

        if resultado.get("cliente_id") is None:
            # Sin base no hay suscripción que transicionar, así que no hay
            # checkout que confirmar: el alta queda para activación manual.
            return render_template(
                "gracias.html.j2", **generador.contexto(),
                cliente=None, simulado=cobro_simulado(), alta=resultado,
                empresa=datos.get("empresa", ""), email=datos.get("email", ""),
                plan=planes.plan(datos.get("plan") or "semanal"))
        return redirect(url_for("checkout", cliente_id=resultado["cliente_id"]))

    # ── cobro (simulado) ─────────────────────────────────────────────
    @app.get("/checkout/<int:cliente_id>")
    def checkout(cliente_id: int):
        con = conectar()
        try:
            cliente = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (cliente_id,))
            if not cliente:
                abort(404)
            competidores = db.competidores_de(con, cliente_id)
            verificaciones = _verificar(competidores)
        finally:
            con.close()

        datos_plan = planes.plan(db.valor(cliente, "plan"))
        return render_template(
            "checkout.html.j2",
            **generador.contexto(),
            cliente=cliente,
            plan=datos_plan,
            precio=planes.precio_usd(cliente),
            competidores=competidores,
            verificaciones=verificaciones,
            simulado=cobro_simulado(),
            enlace_pago=(datos_plan or {}).get("enlace_pago") or "",
        )

    @app.post("/checkout/<int:cliente_id>/confirmar")
    def confirmar(cliente_id: int):
        if not cobro_simulado():
            # Con cobro real, quien activa es el webhook de la pasarela, no
            # un POST desde el navegador: si no, cualquiera se activa solo.
            abort(403, "El cobro real se confirma desde la pasarela, no desde acá")
        con = conectar()
        try:
            with con:
                altas.activar(con, cliente_id, referencia="SIMULADO",
                              proveedor="manual")
        except altas.AltaInvalida:
            abort(404)
        finally:
            con.close()
        return redirect(url_for("gracias", cliente_id=cliente_id))

    @app.get("/gracias/<int:cliente_id>")
    def gracias(cliente_id: int):
        con = conectar()
        try:
            cliente = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (cliente_id,))
        finally:
            con.close()
        if not cliente:
            abort(404)
        return render_template(
            "gracias.html.j2",
            **generador.contexto(),
            cliente=cliente,
            simulado=cobro_simulado(),
            alta=None,
            plan=planes.plan(db.valor(cliente, "plan")),
            empresa=cliente["nombre_empresa"],
            email=cliente["contacto_email"],
        )

    @app.get("/salud")
    def salud():
        """Para que el hosting sepa si el proceso está vivo."""
        estado = {
            "ok": True,
            "deposito": deposito.nombre,
            "cobro": "simulado" if cobro_simulado() else "real",
        }
        faltan = deposito.pendientes()
        if faltan:
            # Configuración incompleta es un 500 a propósito: el formulario
            # está publicado y no puede guardar nada. Mejor que el hosting lo
            # marque caído que descubrirlo por un alta perdida.
            return {**estado, "ok": False, "falta_configurar": faltan}, 500
        if deposito.nombre == "sqlite":
            con = conectar()
            try:
                estado["clientes"] = db.fila(con, "SELECT COUNT(*) AS n FROM clientes")["n"]
            except sqlite3.Error as e:
                return {**estado, "ok": False, "error": str(e)}, 500
            finally:
                con.close()
        return estado

    return app


def _contacto_visible() -> str:
    """WhatsApp o correo de config/landing.yaml, para el mensaje de error."""
    contacto = config.config_landing().get("contacto") or {}
    if contacto.get("whatsapp"):
        return f"WhatsApp {contacto['whatsapp']}"
    return contacto.get("email") or ""


def _pagina(titulo: str, mensaje: str, volver: bool = False) -> str:
    return render_template(
        "mensaje.html.j2",
        **generador.contexto(), titulo_pagina=titulo, mensaje=mensaje, volver=volver)


def _verificar(competidores: list) -> list[dict[str, Any]]:
    """Corre la verificación de anuncios si está habilitada.

    Apagada por defecto: cada verificación llama al scraper y eso cuesta
    plata por anuncio. Un formulario público sin esto apagado es una forma
    de que un desconocido gaste su crédito de Apify.
    """
    if (config.env("PULSERIVAL_VERIFICAR_ALTA") or "0") != "1":
        return []
    salida = []
    for comp in competidores:
        datos = dict(comp)
        for plataforma in ("meta", "google"):
            from ..pipeline import _tiene_datos_para

            if not _tiene_datos_para(datos, plataforma):
                continue
            salida.append({"competidor": datos["nombre"],
                           **altas.verificar_competidor(datos, plataforma)})
    return salida


# Límite simple por IP, en memoria. No sobrevive a un reinicio y no sirve
# para varios procesos, pero para un formulario de alta de bajo volumen
# alcanza y no agrega una dependencia.
_VISTAS: dict[str, list[float]] = {}
_VENTANA_SEG = 60
_MAXIMO = 5


def _demasiadas_peticiones(ip: str | None) -> bool:
    ahora = time.time()
    marcas = [t for t in _VISTAS.get(ip or "?", []) if ahora - t < _VENTANA_SEG]
    marcas.append(ahora)
    _VISTAS[ip or "?"] = marcas
    return len(marcas) > _MAXIMO


app = None


def wsgi():
    """Punto de entrada para gunicorn: `gunicorn 'pulserival.web.app:wsgi()'`."""
    return crear_app()
