"""Servidor web de PulseRival.

Sirve tres cosas y nada más:
  1. la landing, con los planes que salen de config/planes.yaml;
  2. el alta: el formulario que antes había que cargar a mano con
     `cli clientes agregar`;
  3. el cobro. Con PULSERIVAL_COBRO=paddle el checkout abre Paddle y quien
     activa la suscripción es el webhook (/webhooks/paddle), nunca el
     navegador. Sin esa variable el cobro es SIMULADO: una pantalla que marca
     la suscripción como pagada sin mover plata, y lo dice en pantalla. El
     detalle de qué significa cada evento de Paddle está en pulserival/pagos.py.

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

import json
import os
import secrets
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from flask import (Flask, abort, redirect, render_template, request,
                   send_from_directory, url_for)
from jinja2 import ChoiceLoader, FileSystemLoader

from .. import altas, config, db, marca as marca_mod, pagos, planes
from ..landing import generador, legal
from . import deposito as deposito_mod
from . import planificador as plan_mod
from .panel import PLANTILLAS as PLANTILLAS_PANEL
from .panel import panel as plano_panel

# Sin PULSERIVAL_COBRO=paddle el checkout es una simulación y lo dice en
# pantalla. La decisión vive en pagos.modo_cobro().
def cobro_simulado() -> bool:
    return pagos.modo_cobro() == "simulado"


def crear_app(ruta_db: str | None = None, deposito=None) -> Flask:
    # Las plantillas son las mismas que usa la landing estática: una sola
    # copia del HTML para los dos modos.
    app = Flask(__name__, template_folder=str(generador.PLANTILLAS))
    # Escapar siempre. Flask lo hace solo con las plantillas que terminan en
    # .html, y las de este proyecto terminan en .html.j2: sin esta línea, el
    # nombre de empresa que alguien escribe en el formulario público se
    # pintaba como HTML en el checkout y en el panel de administración. Tiene
    # que ir antes de que se cree el entorno de Jinja.
    app.jinja_options = {**app.jinja_options, "autoescape": True}
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
    # una base sin tablas. Es idempotente, así que también sirve de migración
    # en cada deploy; si hay que reconstruir `clientes`, antes deja una copia
    # en respaldos/migraciones/ (ver db.migrar).
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
            "simulado": cobro_simulado(),
            "modo_servidor": True,
            "accion_alta": url_for("alta"),
            "inicio_url": url_for("inicio"),
            "aplicar_url": url_for("aplicar"),
            "marca_url": "/marca",
        })
        return ctx

    @app.get("/marca/<path:archivo>")
    def activo_marca(archivo: str):
        """Logos, favicons y la imagen para compartir.

        Se sirven desde acá y no desde /static para tener un solo lugar con
        una sola regla de caché. Son archivos que no cambian: un año.
        """
        respuesta = send_from_directory(marca_mod.ACTIVOS, archivo)
        respuesta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return respuesta

    @app.get("/")
    def inicio():
        return render_template("index.html.j2", **_contexto_publico())

    def _pagina_legal(clave: str):
        return render_template("legal.html.j2", **_contexto_publico(),
                               legal=legal.pagina(clave))

    # Paddle revisa que estas tres existan, sean públicas y respondan 200
    # antes de aprobar el dominio.
    for _clave, (_ruta, _, _) in legal.PAGINAS.items():
        app.add_url_rule(_ruta, f"legal_{_clave}",
                         lambda clave=_clave: _pagina_legal(clave))

    @app.get("/aplicar")
    def aplicar():
        """El formulario, en su propia página. Al enviarlo se pasa al cobro."""
        ctx = _contexto_publico()
        # El plan y el ciclo llegan por la URL desde los botones de la
        # portada. Si viene uno que no existe se cae al primero en vez de
        # romper: un enlace viejo compartido por ahí no puede dejar a alguien
        # sin poder aplicar, y el resumen de al lado tiene que decir lo mismo
        # que el selector.
        vendibles = ctx.get("planes_checkout") or []
        elegido = next((p for p in vendibles if p.get("clave") == request.args.get("plan")),
                       vendibles[0] if vendibles else None)
        ctx["plan_elegido"] = (elegido or {}).get("clave")
        ciclos = (elegido or {}).get("ciclos") or {}
        pedido = request.args.get("ciclo")
        ctx["ciclo_elegido"] = pedido if pedido in ciclos else next(iter(ciclos), None)
        ctx["forma_competidores"] = generador.competidores_del_formulario(ctx)
        return render_template("aplicar.html.j2", **ctx)

    def _origen(datos) -> str | None:
        """Cómo nos conoció, de la encuesta del último paso.

        Cuando eligió "Otro" vale lo que escribió, no la etiqueta: "Otro" a
        secas no sirve para decidir dónde invertir el tiempo, que es lo único
        para lo que existe esta pregunta.
        """
        elegido = (datos.get("origen") or "").strip()
        otro = (datos.get("origen_otro") or "").strip()
        if elegido == "otro":
            return otro or None
        return elegido or None

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

        # Desde la web solo se contratan los planes con checkout. El POST lo
        # puede armar cualquiera con curl: sin esto, alguien se da de alta en
        # el plan Custom o en una prueba gratis que ya no se ofrece.
        vendibles = {p["clave"]: p for p in planes.con_checkout()}
        plan = datos.get("plan") or next(iter(vendibles), "")
        if plan not in vendibles:
            return _pagina("No pudimos completar el registro",
                           "Ese plan no se contrata desde la página. Escríbanos"
                           + (f" a {_contacto_visible()}" if _contacto_visible() else "")
                           + " y lo armamos con usted.", volver=True), 400
        # La Ley 8968 (art. 5) exige el consentimiento expreso y por escrito
        # antes de guardar datos personales. La casilla la valida el
        # navegador, pero el POST lo puede armar cualquiera sin ella.
        if datos.get("acepto") != "si":
            return _pagina("Falta aceptar los términos",
                           "Para registrar su solicitud necesitamos que acepte los "
                           "Términos y la Política de privacidad. Vuelva al formulario "
                           "y marque la casilla del final.", volver=True), 400
        tope = vendibles[plan].get("competidores_max")
        if tope and len(competidores) > int(tope):
            return _pagina("No pudimos completar el registro",
                           f"El plan incluye hasta {tope} competidores.", volver=True), 400

        # Lo único obligatorio: el nombre de la empresa y el correo. Si faltan,
        # `altas.preparar` lo dice. Los competidores, si no vienen, se deducen.
        empresa = (datos.get("empresa") or "").strip()
        email = (datos.get("email") or "").strip()
        try:
            resultado = deposito.guardar({
                "empresa": empresa,
                "email": email,
                "competidores": competidores,
                "plan": plan,
                "ciclo": datos.get("ciclo"),
                "contacto": datos.get("contacto"),
                "whatsapp": datos.get("whatsapp"),
                "industria": datos.get("industria"),
                "notas": datos.get("contexto"),
                "origen": _origen(datos),
                "consentimiento": legal.version() or "sin versión",
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
                empresa=empresa, email=email,
                plan=planes.plan(plan))
        cid = resultado["cliente_id"]
        return redirect(url_for("checkout", cliente_id=cid, t=_firma(cid)))

    # ── cobro ────────────────────────────────────────────────────────
    def _firma(cliente_id: int) -> str:
        return pagos.firma_cliente(cliente_id, app.secret_key)

    def _cliente_firmado(cliente_id: int) -> sqlite3.Row:
        """El cliente de la URL, si la URL trae su firma. Si no, 404.

        Sin la firma estas páginas eran /checkout/1, /checkout/2...: cambiando
        el número se veían el correo y los competidores de otro cliente.
        Responde 404 y no 403 para no confirmar que ese id existe.
        """
        if not pagos.firma_valida(cliente_id, request.values.get("t"), app.secret_key):
            abort(404)
        con = conectar()
        try:
            cliente = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (cliente_id,))
        finally:
            con.close()
        if not cliente:
            abort(404)
        return cliente

    def _datos_paddle(cliente: sqlite3.Row) -> dict[str, Any]:
        """Lo que la página de checkout le pasa a Paddle.js."""
        datos_plan = planes.plan(db.valor(cliente, "plan"))
        # Un precio por forma de pago: mes a mes o el año. La persona elige en
        # la página y el guion abre Paddle con el de la opción marcada.
        precios = {clave: pagos.precio_paddle(datos_plan, clave)
                   for clave in (datos_plan.get("ciclos") or {})}
        return {
            "entorno": pagos.entorno(),
            "token": config.env("PADDLE_CLIENT_TOKEN") or "",
            "precios": {k: v for k, v in precios.items() if v},
            "email": cliente["contacto_email"],
            # Viaja por el navegador y vuelve en el webhook: es lo que dice de
            # qué cliente es el pago. La firma impide cambiarle el id.
            "custom_data": {"cliente_id": str(cliente["id"]),
                            "firma": _firma(int(cliente["id"]))},
            "faltan": pagos.pendientes(),
        }

    @app.get("/checkout/<int:cliente_id>")
    def checkout(cliente_id: int):
        cliente = _cliente_firmado(cliente_id)
        con = conectar()
        try:
            competidores = db.competidores_de(con, cliente_id)
            verificaciones = _verificar(competidores)
        finally:
            con.close()
        datos_plan = planes.plan(db.valor(cliente, "plan"))
        return render_template(
            "checkout.html.j2",
            formas=_formas_de_pago(datos_plan),
            ciclo_actual=db.valor(cliente, "ciclo_facturacion"),
            **generador.contexto(),
            cliente=cliente,
            plan=datos_plan,
            ciclo=planes.ciclo(datos_plan, db.valor(cliente, "ciclo_facturacion")),
            precio=planes.precio_usd(cliente),
            competidores=competidores,
            verificaciones=verificaciones,
            simulado=cobro_simulado(),
            paddle=None if cobro_simulado() else _datos_paddle(cliente),
            firma=_firma(cliente_id),
        )

    @app.post("/checkout/<int:cliente_id>/confirmar")
    def confirmar(cliente_id: int):
        if not cobro_simulado():
            # Con cobro real, quien activa es el webhook de Paddle, no un POST
            # desde el navegador: si no, cualquiera se activa solo.
            abort(403, "El cobro real se confirma desde Paddle, no desde acá")
        cliente = _cliente_firmado(cliente_id)
        # La forma de pago se elige en el checkout. En el cobro real la anota
        # el webhook según el precio que se cobró; acá se anota a mano.
        ciclos = planes.plan(db.valor(cliente, "plan")).get("ciclos") or {}
        ciclo = request.form.get("ciclo")
        con = conectar()
        try:
            with con:
                if ciclo in ciclos:
                    db.actualizar(con, "clientes", cliente_id, {"ciclo_facturacion": ciclo})
                altas.activar(con, cliente_id, referencia="SIMULADO",
                              proveedor="manual")
        except altas.AltaInvalida as e:
            return _pagina("No se pudo activar", str(e), volver=True), 409
        finally:
            con.close()
        return redirect(url_for("gracias", cliente_id=cliente_id, t=_firma(cliente_id)))

    @app.get("/gracias/<int:cliente_id>")
    def gracias(cliente_id: int):
        cliente = _cliente_firmado(cliente_id)
        con = conectar()
        try:
            sin_competidores = not db.competidores_de(con, cliente_id)
        finally:
            con.close()
        return render_template(
            "gracias.html.j2",
            **generador.contexto(),
            cliente=cliente,
            simulado=cobro_simulado(),
            alta=None,
            plan=planes.plan(db.valor(cliente, "plan")),
            empresa=cliente["nombre_empresa"],
            email=cliente["contacto_email"],
            sin_competidores=sin_competidores,
        )

    @app.get("/pagar")
    def pagar():
        """El "enlace de pago por defecto" que se configura en Paddle.

        Los links de pago que se arman a mano en el panel de Paddle (el plan
        Custom) son esta URL con `?_ptxn=txn_...`. La página no tiene que
        hacer nada más que cargar Paddle.js: Paddle.js ve el parámetro y abre
        solo el checkout de esa transacción. Sin esta página, esos links
        llevan a un 404 y no se puede cobrar ningún Custom.

        Va en Paddle > Checkout > Checkout settings > Default payment link, y
        el dominio tiene que estar aprobado por Paddle (en sandbox no hace
        falta).
        """
        return render_template(
            "pagar.html.j2", **generador.contexto(),
            paddle={"entorno": pagos.entorno(),
                    "token": config.env("PADDLE_CLIENT_TOKEN") or ""},
            transaccion=request.args.get("_ptxn"),
        )

    @app.post("/webhooks/paddle")
    def webhook_paddle():
        """Las notificaciones de Paddle. Lo único que activa o corta un cliente.

        Paddle reintenta todo lo que no sea un 200 en menos de 5 segundos
        (60 veces en 3 días en producción), así que:
          - firma mala o sin secreto: no 200, para que no se pierda si es un
            error de configuración que se arregla después;
          - evento que no se sabe de quién es: 200 igual, queda guardado en
            eventos_pago. Reintentarlo no lo va a resolver.
        """
        secreto = config.env("PADDLE_WEBHOOK_SECRET")
        if not secreto:
            app.logger.error("Llegó un webhook de Paddle y no hay PADDLE_WEBHOOK_SECRET")
            return {"ok": False, "error": "webhook sin configurar"}, 503
        if deposito.nombre != "sqlite":
            # Sin base no hay a quién activar: este webhook escribe en SQLite.
            return {"ok": False, "error": "el webhook necesita la base de datos"}, 503
        # El cuerpo tal cual llegó, en bytes: la firma es sobre esos bytes, y
        # leerlo como JSON y volver a serializarlo la rompe.
        cuerpo = request.get_data(cache=True)
        try:
            pagos.verificar_firma(cuerpo, request.headers.get("Paddle-Signature"), secreto)
        except pagos.FirmaInvalida as e:
            app.logger.warning("Webhook de Paddle rechazado: %s", e)
            return {"ok": False, "error": "firma inválida"}, 400
        try:
            evento = json.loads(cuerpo)
        except ValueError:
            return {"ok": False, "error": "el cuerpo no es JSON"}, 400
        con = conectar()
        try:
            with con:
                resultado = pagos.aplicar_evento(con, evento, cuerpo.decode("utf-8"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        except sqlite3.Error as e:
            # Error de nuestro lado: que Paddle reintente.
            app.logger.error("No se pudo guardar el evento de Paddle: %s", e)
            return {"ok": False, "error": "no se pudo guardar"}, 500
        finally:
            con.close()
        if resultado["resultado"] == "sin_cliente":
            app.logger.warning("Evento de Paddle sin cliente: %s %s",
                               evento.get("event_type"), evento.get("event_id"))
        return {"ok": True, **resultado}

    @app.get("/salud")
    def salud():
        """Para que el hosting sepa si el proceso está vivo."""
        from .panel import clave_configurada

        estado = {
            "ok": True,
            "deposito": deposito.nombre,
            "cobro": ("simulado" if cobro_simulado()
                      else f"paddle ({pagos.entorno()})"),
            "panel": "habilitado" if clave_configurada() else "sin PULSERIVAL_PANEL_CLAVE",
            "sesiones": "efímeras" if app.config["SECRETO_EFIMERO"] else "persistentes",
        }
        faltan = deposito.pendientes()
        if not cobro_simulado():
            # Un pago que entra con el webhook sin configurar no activa a
            # nadie: el cliente pagó y no recibe nada. Mejor que el hosting lo
            # marque caído antes de que pase.
            faltan = faltan + pagos.pendientes()
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
    """El correo de soporte (o el WhatsApp si no hay correo), para los
    mensajes de error que dicen "escríbanos"."""
    contacto = config.config_landing().get("contacto") or {}
    if contacto.get("email"):
        return contacto["email"]
    return f"WhatsApp {contacto['whatsapp']}" if contacto.get("whatsapp") else ""


def _formas_de_pago(datos_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Las opciones de pago del checkout, en el orden de config/planes.yaml.

    Con una sola forma no hay nada que elegir y no se muestra el bloque.
    """
    ciclos = datos_plan.get("ciclos") or {}
    if len(ciclos) < 2:
        return []
    return [{"clave": clave, "titulo": c.get("nombre") or clave, **c}
            for clave, c in ciclos.items()]


def _pagina(titulo: str, mensaje: str, volver: bool = False) -> str:
    return render_template(
        "mensaje.html.j2",
        **generador.contexto(), titulo_pagina=titulo, mensaje=mensaje, volver=volver)


def _con_marca(datos: dict[str, Any]) -> dict[str, Any]:
    datos.setdefault("marca_url", "/marca")
    return datos


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
