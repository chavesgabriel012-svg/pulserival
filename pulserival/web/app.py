"""Servidor web de PulseRival.

Sirve tres cosas y nada más:
  1. la landing, con los planes que salen de config/planes.yaml;
  2. el alta: el formulario que antes había que cargar a mano con
     `cli clientes agregar`;
  3. el cobro, HOY SIMULADO. No hay pasarela conectada: el checkout es una
     pantalla que marca la suscripción como pagada sin mover plata. Está
     marcado como simulación en la interfaz para que nadie lo confunda.

Y el panel de revisión, en `panel.py`: la bandeja de reportes pendientes,
editar, aprobar, enviar o descartar, y el gasto. El panel solo se sirve si hay
contraseña configurada.

Sobre dónde queda el alta: depende de PULSERIVAL_DEPOSITO, y eso decide qué
hosting sirve. Con `sqlite` (por defecto) escribe en la base que apunte
`PULSERIVAL_DB`, y el proceso necesita un disco que persista entre reinicios.
Con `github` escribe un YAML en el repositorio y no toca ninguna base, que es
lo que permite correr en un hosting serverless como Vercel. El detalle está en
`pulserival/web/deposito.py`.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from flask import Flask, abort, redirect, render_template, request, url_for
from jinja2 import ChoiceLoader, FileSystemLoader

from .. import altas, config, db, planes
from ..landing import generador
from . import deposito as deposito_mod
from . import planificador as plan_mod
from .panel import PLANTILLAS as PLANTILLAS_PANEL
from .panel import panel as plano_panel

# El cobro real todavía no existe. Mientras esta bandera esté encendida, el
# checkout es una simulación y lo dice en pantalla.
def cobro_simulado() -> bool:
    return (config.env("PULSERIVAL_COBRO") or "simulado").lower() != "real"


def crear_app(ruta_db: str | None = None, deposito=None) -> Flask:
    # Las plantillas son las mismas que usa la landing estática: una sola
    # copia del HTML para los dos modos.
    app = Flask(__name__, template_folder=str(generador.PLANTILLAS))
    # Dos carpetas de plantillas: las de la landing se comparten con el
    # generador estático, las del panel son solo del servidor.
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(str(generador.PLANTILLAS)),
        FileSystemLoader(str(PLANTILLAS_PANEL)),
    ])
    app.config["RUTA_DB"] = ruta_db or str(config.ruta_db())

    # La clave firma la cookie de sesión del panel. Si no está configurada se
    # genera una al azar por proceso: la sesión se cae en cada reinicio, que es
    # molesto, pero una clave fija escrita en el código dejaría que cualquiera
    # que lea el repositorio se firme una sesión de administrador.
    app.secret_key = config.env("PULSERIVAL_SECRET") or secrets.token_urlsafe(32)
    app.config["SECRETO_EFIMERO"] = not config.env("PULSERIVAL_SECRET")
    app.permanent_session_lifetime = timedelta(hours=12)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Sin HTTPS la cookie no viaja, y en local no hay HTTPS. Se activa
        # cuando el servidor está publicado.
        SESSION_COOKIE_SECURE=(config.env("PULSERIVAL_HTTPS") or "0") == "1",
    )

    def conectar() -> sqlite3.Connection:
        con = sqlite3.connect(app.config["RUTA_DB"])
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    deposito = deposito or deposito_mod.obtener(conectar)
    app.config["DEPOSITO"] = deposito
    # El panel abre su propia conexión por petición, igual que las rutas de acá.
    app.config["CONECTAR"] = conectar
    app.register_blueprint(plano_panel)

    # Crear el esquema si falta y correr las migraciones. En un servidor nuevo
    # el disco arranca vacío, y sin esto la primera petición se encuentra con
    # una base sin tablas. Es idempotente (todo es CREATE TABLE IF NOT EXISTS
    # y columnas aditivas), así que también sirve de migración en cada deploy.
    if deposito.nombre == "sqlite":
        db.inicializar(Path(app.config["RUTA_DB"]))

    # El cron vive en este mismo proceso: en Fly y en Railway el disco se monta
    # en un solo contenedor, así que un ciclo en otro contenedor escribiría en
    # otra base. Apagado por defecto para que los tests y la máquina local no
    # se pongan a correr ciclos solos; en el servidor se enciende con
    # PULSERIVAL_PLANIFICADOR=1.
    app.config["PLANIFICADOR"] = None
    if plan_mod.habilitado():
        planificador = plan_mod.Planificador(conectar)
        planificador.arrancar()
        app.config["PLANIFICADOR"] = planificador

    # ── landing ──────────────────────────────────────────────────────
    def _contexto_publico() -> dict[str, Any]:
        ctx = generador.contexto()
        ctx.update({
            "modo_servidor": True,
            "accion_alta": url_for("alta"),
            "inicio_url": url_for("inicio"),
            "aplicar_url": url_for("aplicar"),
        })
        return ctx

    @app.get("/")
    def inicio():
        return render_template("index.html.j2", **_contexto_publico())

    @app.get("/aplicar")
    def aplicar():
        """El formulario, en su propia página. Más adelante acá va el cobro."""
        ctx = _contexto_publico()
        # El plan llega por la URL desde el botón de cada plan. Si viene uno
        # que no existe, se cae al primero en vez de romper: un enlace viejo
        # compartido por ahí no puede dejar a alguien sin poder aplicar.
        pedido = request.args.get("plan")
        catalogo = ctx.get("planes") or []
        elegido = next((p for p in catalogo if p.get("clave") == pedido), None)
        ctx["plan_elegido"] = (elegido or {}).get("clave")
        ctx["resumen_plan"] = elegido
        return render_template("aplicar.html.j2", **ctx)

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
        from .panel import clave_configurada

        estado = {
            "ok": True,
            "deposito": deposito.nombre,
            "cobro": "simulado" if cobro_simulado() else "real",
            "panel": "habilitado" if clave_configurada() else "sin PULSERIVAL_PANEL_CLAVE",
            "sesiones": "efímeras" if app.config["SECRETO_EFIMERO"] else "persistentes",
        }
        faltan = deposito.pendientes()
        if faltan:
            # Configuración incompleta es un 500 a propósito: el formulario
            # está publicado y no puede guardar nada. Mejor que el hosting lo
            # marque caído que descubrirlo por un alta perdida.
            return {**estado, "ok": False, "falta_configurar": faltan}, 500
        if deposito.nombre == "sqlite":
            estado["disco"] = _revisar_disco(app.config["RUTA_DB"])
            con = conectar()
            try:
                estado["clientes"] = db.fila(con, "SELECT COUNT(*) AS n FROM clientes")["n"]
                estado.update(plan_mod.estado(con))
            except sqlite3.Error as e:
                return {**estado, "ok": False, "error": str(e)}, 500
            finally:
                con.close()
        return estado

    return app


def _revisar_disco(ruta_db: str) -> str:
    """¿La base está en un disco que sobrevive al próximo deploy?

    Es la falla más cara que puede tener este servidor, y la más silenciosa:
    si el disco está montado en otra ruta, SQLite escribe igual —en el sistema
    de archivos del contenedor— y todo funciona perfecto hasta el deploy
    siguiente, que se lleva los clientes y el historial de anuncios. Y ese
    historial es lo único que permite decir "esto es nuevo": una vez perdido no
    se recupera, porque las plataformas solo muestran lo que está activo hoy.

    Solo se avisa cuando se está corriendo en un hosting. En la máquina local
    la base no está en ningún disco montado y eso es lo normal.
    """
    carpeta = Path(ruta_db).parent
    try:
        montado = os.path.ismount(carpeta)
    except OSError:
        return f"{carpeta}: no se pudo comprobar"
    if montado:
        return f"{carpeta}: disco montado, sobrevive a los deploys"
    # RAILWAY_* y FLY_APP_NAME solo existen dentro de esos hostings.
    en_hosting = any(os.environ.get(v) for v in
                     ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_NAME", "FLY_APP_NAME"))
    if en_hosting:
        return (f"ATENCIÓN · {carpeta} NO es un disco montado: la base se borra "
                f"en el próximo deploy. Revise que el volumen esté montado "
                f"exactamente en {carpeta}")
    return f"{carpeta}: sin disco montado (normal fuera de un servidor)"


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
