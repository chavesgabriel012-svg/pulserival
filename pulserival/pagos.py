"""El cobro con Paddle: qué significa cada evento para un cliente.

Este módulo no sabe nada de HTTP. La ruta del webhook (pulserival/web/app.py)
verifica la firma y le pasa el evento ya leído; acá se decide qué cliente es y
qué le cambia. Separado así se prueba entero sin levantar un servidor.

Lo que hace difícil un webhook, y cómo se resuelve cada cosa:

  - **Llega dos veces.** Paddle entrega "al menos una vez". Cada evento tiene
    un `event_id`; se guarda en `eventos_pago` y uno repetido no hace nada.
  - **Llega desordenado.** Paddle no garantiza el orden: un "activa" viejo
    puede llegar después de un "cancelada" nuevo. Se compara `occurred_at`
    con el del último evento aplicado a ese cliente y el viejo no pisa.
  - **Hay que saber de quién es.** Tres caminos, del más firme al más débil:
      1. el id de la suscripción (sub_...) o de la transacción (txn_...) que
         ya se guardó para ese cliente;
      2. `custom_data` del checkout, que lleva el id del cliente y una firma.
         La firma importa: `custom_data` lo arma el navegador, y sin ella
         cualquiera podría pagar poniendo el id de otro cliente y quedarse con
         su suscripción.
    Un plan Custom se cobra con un link armado a mano en el panel de Paddle,
    sin pasar por nuestro checkout: lo que lo reconoce es el txn_ que se
    registró con `cli clientes agregar --paddle-transaccion`.
  - **El estado de Paddle no es el nuestro.** Se guardan los dos: el de Paddle
    tal cual (`paddle_estado`) y su traducción (`estado_suscripcion`), que es
    lo que decide si se recolecta y se reporta.

Lo que este módulo NO hace: cobrar, reembolsar ni cancelar. Todo eso se hace
en el panel de Paddle; acá solo se escucha lo que Paddle cuenta que pasó.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from . import config, db, planes

ENTORNOS = ("sandbox", "production")

# Paddle recomienda 5 segundos de tolerancia entre la firma y el reloj propio.
# Acá se acepta más porque no abre nada: un evento repetido no hace nada (se
# guarda su event_id) y uno viejo no pisa a uno nuevo (se compara occurred_at).
# Con 5 segundos, un reloj del servidor un poco corrido rechazaría entregas
# legítimas, y Paddle las reintenta solo un rato.
TOLERANCIA_FIRMA_SEG = 300

# Estado de la suscripción en Paddle -> el nuestro.
#   past_due: falló la renovación y Paddle está reintentando el cobro. No se
#     reporta mientras tanto; si el cobro entra, Paddle avisa que volvió a
#     'active' y el reporte sale en la corrida siguiente, no se pierde.
#   trialing: este lanzamiento no tiene prueba gratis, pero si se activa en
#     Paddle, quien está probando recibe reportes.
TRADUCCION = {
    "active": "activa",
    "trialing": "activa",
    "past_due": "vencida",
    "paused": "pausada",
    "canceled": "cancelada",
}

# Intervalo de facturación de Paddle -> nuestro ciclo.
CICLOS = {"month": "mensual", "year": "anual"}


class FirmaInvalida(Exception):
    """El cuerpo no lo firmó Paddle con nuestro secreto, o la firma es vieja."""


# ── configuración ────────────────────────────────────────────────────
def modo_cobro() -> str:
    """simulado | paddle. 'real' es el nombre viejo de paddle."""
    valor = (config.env("PULSERIVAL_COBRO") or "simulado").strip().lower()
    return "paddle" if valor in ("paddle", "real") else "simulado"


def entorno() -> str:
    """sandbox salvo que se pida production con esa palabra exacta.

    Por defecto sandbox a propósito: cobrar de verdad tiene que ser una
    decisión explícita, no lo que pasa si falta una variable.
    """
    valor = (config.env("PADDLE_ENTORNO") or "sandbox").strip().lower()
    return valor if valor in ENTORNOS else "sandbox"


def precio_paddle(datos_plan: dict[str, Any], ciclo: str | None) -> str | None:
    """El id del precio en Paddle (pri_...) para ese plan y ciclo, en el entorno actual."""
    por_entorno = ((datos_plan.get("paddle") or {}).get(entorno()) or {})
    return (por_entorno.get(ciclo or "") or "").strip() or None


def pendientes() -> list[str]:
    """Lo que falta configurar para cobrar con Paddle. Vacío = listo."""
    faltan = []
    crudo = (config.env("PADDLE_ENTORNO") or "").strip().lower()
    if crudo and crudo not in ENTORNOS:
        faltan.append(f"PADDLE_ENTORNO='{crudo}' no es válido (sandbox o production): "
                      "se está usando sandbox")
    token = config.env("PADDLE_CLIENT_TOKEN") or ""
    if not token:
        faltan.append("PADDLE_CLIENT_TOKEN (Paddle > Developer tools > Authentication)")
    else:
        # Un token de un entorno no funciona en el otro, y el error que da
        # Paddle.js en el navegador no dice por qué.
        esperado = "test_" if entorno() == "sandbox" else "live_"
        if not token.startswith(esperado):
            faltan.append(f"PADDLE_CLIENT_TOKEN no es de {entorno()} (tendría que "
                          f"empezar con '{esperado}')")
    if not config.env("PADDLE_WEBHOOK_SECRET"):
        faltan.append("PADDLE_WEBHOOK_SECRET (Paddle > Developer tools > Notifications): "
                      "sin esto los pagos entran pero nadie se activa")
    if not config.env("PULSERIVAL_SECRET"):
        faltan.append("PULSERIVAL_SECRET: firma el id del cliente que viaja en el "
                      "checkout; sin un valor fijo, una firma de antes de un reinicio "
                      "no se reconoce después")
    for datos in planes.con_checkout():
        for ciclo in (datos.get("ciclos") or {}):
            if not precio_paddle(datos, ciclo):
                faltan.append(f"config/planes.yaml: falta el precio de Paddle de "
                              f"'{datos['clave']}' {ciclo} en {entorno()}")
    return faltan


# ── la firma del id de cliente que viaja por el navegador ───────────
def firma_cliente(cliente_id: int, secreto: str | None = None) -> str:
    """Firma del id de cliente para el checkout y para las URLs de checkout
    y gracias. Sin ella esas URLs son un número correlativo: cambiándolo se
    ven el correo y los competidores de otro cliente.

    `secreto` es la clave de la app web. Con cobro real tiene que ser
    PULSERIVAL_SECRET (lo exige `pendientes()`): la firma viaja a Paddle en
    `custom_data` y vuelve en el webhook, quizás después de un reinicio.
    """
    clave = (secreto or config.env("PULSERIVAL_SECRET") or "").encode("utf-8")
    if not clave:
        raise RuntimeError("Falta PULSERIVAL_SECRET para firmar el id del cliente")
    return hmac.new(clave, f"cliente:{int(cliente_id)}".encode(), hashlib.sha256).hexdigest()[:32]


def firma_valida(cliente_id: Any, firma: Any, secreto: str | None = None) -> bool:
    try:
        esperada = firma_cliente(int(cliente_id), secreto)
    except (TypeError, ValueError, RuntimeError):
        return False
    return hmac.compare_digest(esperada, str(firma or ""))


# ── la firma del webhook ─────────────────────────────────────────────
def verificar_firma(cuerpo: bytes, cabecera: str | None, secreto: str,
                    ahora: float | None = None) -> None:
    """Comprueba que Paddle firmó este cuerpo, tal cual llegó.

    La cabecera es `Paddle-Signature: ts=<unix>;h1=<hex>`. Lo firmado es
    `ts + ":" + cuerpo crudo` con HMAC-SHA256. El cuerpo tiene que ser el que
    llegó byte por byte: leerlo como JSON y volver a serializarlo cambia los
    espacios y la firma deja de coincidir (la causa más común de fallas,
    según Paddle). Mientras se rota el secreto puede haber más de un h1.
    """
    if not cabecera:
        raise FirmaInvalida("Falta la cabecera Paddle-Signature")
    partes: dict[str, list[str]] = {}
    for trozo in cabecera.split(";"):
        clave, _, valor = trozo.strip().partition("=")
        if clave and valor:
            partes.setdefault(clave, []).append(valor)
    ts = (partes.get("ts") or [""])[0]
    firmas = partes.get("h1") or []
    if not ts.isdigit() or not firmas:
        raise FirmaInvalida("La cabecera Paddle-Signature no tiene ts y h1")
    ahora = time.time() if ahora is None else ahora
    if abs(ahora - int(ts)) > TOLERANCIA_FIRMA_SEG:
        raise FirmaInvalida("La firma es de hace demasiado tiempo")
    esperada = hmac.new(secreto.encode("utf-8"), ts.encode("ascii") + b":" + cuerpo,
                        hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(esperada, f) for f in firmas):
        raise FirmaInvalida("La firma no coincide")


def firmar(cuerpo: bytes, secreto: str, ts: int | None = None) -> str:
    """La cabecera que mandaría Paddle. Para los tests y para probar a mano."""
    ts = int(time.time()) if ts is None else ts
    h1 = hmac.new(secreto.encode("utf-8"), str(ts).encode() + b":" + cuerpo,
                  hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


# ── aplicar un evento ────────────────────────────────────────────────
def aplicar_evento(con: sqlite3.Connection, evento: dict[str, Any],
                   crudo: str | None = None) -> dict[str, Any]:
    """Aplica un evento de Paddle a la base. Devuelve qué hizo.

    No hace commit: la ruta del webhook lo envuelve en una transacción, así
    el registro del evento y el cambio del cliente entran juntos o ninguno.
    """
    event_id = str(evento.get("event_id") or "")
    tipo = str(evento.get("event_type") or "")
    datos = evento.get("data") or {}
    if not event_id or not tipo or not isinstance(datos, dict):
        raise ValueError("El evento no tiene event_id, event_type o data")
    ocurrido = _fecha(evento.get("occurred_at"))

    if db.fila(con, "SELECT 1 FROM eventos_pago WHERE event_id = ?", (event_id,)):
        return {"resultado": "repetido", "event_id": event_id}

    cliente, como = _buscar_cliente(con, tipo, datos)
    if cliente is None:
        return _registrar(con, event_id, tipo, ocurrido, None, "sin_cliente",
                          "No se encontró a qué cliente corresponde", evento, crudo)

    cid = int(cliente["id"])
    ultimo = _fecha(db.valor(cliente, "paddle_actualizado_en"))
    viejo = bool(ultimo and ocurrido and ocurrido < ultimo)
    cambios = _ids(cliente, tipo, datos)
    detalle = f"cliente por {como}"

    if viejo:
        # Llegó tarde: no toca el estado. Sí completa ids que falten, que
        # no dependen del orden (el id de una suscripción no cambia).
        if cambios:
            db.actualizar(con, "clientes", cid, cambios)
        return _registrar(con, event_id, tipo, ocurrido, cid, "viejo",
                          f"{detalle}; ocurrió antes que el último aplicado ({ultimo.isoformat()})",
                          evento, crudo)

    estado_antes = db.valor(cliente, "estado_suscripcion")
    efecto = _efecto(tipo, datos, cliente)
    # "aplicado" quiere decir que cambió algo del estado del cliente. Guardar
    # un id que faltaba no es eso: una tarjeta rechazada en el checkout deja
    # el txn_ anotado y nada más, y leerla como "aplicada" confunde.
    resultado = "aplicado" if efecto else "sin_efecto"
    if cambios:
        detalle += f"; ids guardados: {', '.join(sorted(cambios))}"
    cambios.update(efecto)
    # La marca avanza con todo evento que dice el estado completo, aunque no
    # cambie nada. Si no avanzara, un "active" que confirma lo que ya había
    # dejaría la marca atrás, y un "past_due" más viejo que llegue después se
    # aplicaría encima: el cliente quedaría vencido estando al día.
    if ocurrido and (tipo.startswith("subscription.") or tipo == "transaction.completed"):
        cambios["paddle_actualizado_en"] = ocurrido.isoformat()
    if cambios:
        db.actualizar(con, "clientes", cid, cambios)
    if "estado_suscripcion" in cambios and cambios["estado_suscripcion"] != estado_antes:
        detalle += f"; {estado_antes or '(sin estado)'} -> {cambios['estado_suscripcion']}"
    return _registrar(con, event_id, tipo, ocurrido, cid, resultado, detalle, evento, crudo)


def _efecto(tipo: str, datos: dict[str, Any], cliente: Any) -> dict[str, Any]:
    """Qué le cambia el evento al estado del cliente."""
    cambios: dict[str, Any] = {}
    if tipo.startswith("subscription."):
        estado_paddle = str(datos.get("status") or "")
        if estado_paddle:
            cambios["paddle_estado"] = estado_paddle
        nuestro = TRADUCCION.get(estado_paddle)
        if nuestro:
            cambios["estado_suscripcion"] = nuestro
        ciclo = CICLOS.get(((datos.get("billing_cycle") or {}).get("interval") or ""))
        if ciclo and _admite_ciclo(cliente, ciclo):
            cambios["ciclo_facturacion"] = ciclo
    elif tipo == "transaction.completed":
        # El pago entró: el primero (alta) o una renovación. Si el cliente
        # estaba esperando el pago o tenía el cobro caído, queda activo. Una
        # suscripción cancelada no se reactiva por una transacción suelta: si
        # vuelve, Paddle avisa con un evento de la suscripción.
        if db.valor(cliente, "estado_suscripcion") in (None, "pendiente_pago", "vencida"):
            cambios["estado_suscripcion"] = "activa"
            if datos.get("subscription_id") and not db.valor(cliente, "paddle_estado"):
                cambios["paddle_estado"] = "active"
        ciclo = _ciclo_de_transaccion(datos)
        if ciclo and _admite_ciclo(cliente, ciclo):
            cambios["ciclo_facturacion"] = ciclo
    # transaction.payment_failed no cambia nada a propósito: en el checkout
    # es una tarjeta rechazada que la persona reintenta con otra, y en una
    # renovación Paddle avisa aparte con subscription.past_due.

    if cambios.get("estado_suscripcion") == "activa":
        # Un cliente que paga se reporta aunque alguien lo haya desactivado:
        # `aplicar-config` desactiva a los que no están en clientes.yaml, y
        # los que entran por la web no están ahí.
        cambios["activo"] = 1
    return {k: v for k, v in cambios.items() if db.valor(cliente, k) != v}


def _ids(cliente: Any, tipo: str, datos: dict[str, Any]) -> dict[str, Any]:
    """Los ids de Paddle que el evento trae y el cliente todavía no tiene."""
    if tipo.startswith("subscription."):
        sub, txn = datos.get("id"), datos.get("transaction_id")
    else:
        sub, txn = datos.get("subscription_id"), datos.get("id")
    candidatos = {
        "paddle_suscripcion_id": sub,
        "paddle_cliente_id": datos.get("customer_id"),
        "paddle_transaccion_id": txn,
        "pago_proveedor": "paddle",
    }
    return {k: v for k, v in candidatos.items()
            if v and not db.valor(cliente, k)}


def _buscar_cliente(con: sqlite3.Connection, tipo: str,
                    datos: dict[str, Any]) -> tuple[Any, str]:
    if tipo.startswith("subscription."):
        sub, txn = datos.get("id"), datos.get("transaction_id")
    else:
        sub, txn = datos.get("subscription_id"), datos.get("id")
    if sub:
        fila = db.fila(con, "SELECT * FROM clientes WHERE paddle_suscripcion_id = ?", (sub,))
        if fila:
            return fila, "suscripción"
    if txn:
        fila = db.fila(con, "SELECT * FROM clientes WHERE paddle_transaccion_id = ?", (txn,))
        if fila:
            return fila, "transacción"
    extra = datos.get("custom_data") or {}
    if isinstance(extra, dict) and firma_valida(extra.get("cliente_id"), extra.get("firma")):
        fila = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (int(extra["cliente_id"]),))
        # Un cliente que ya tiene otra suscripción no se reasigna por
        # custom_data: eso sería quedarse con la suscripción de otro.
        if fila and (not db.valor(fila, "paddle_suscripcion_id")
                     or db.valor(fila, "paddle_suscripcion_id") == sub):
            return fila, "custom_data"
    return None, ""


def _ciclo_de_transaccion(datos: dict[str, Any]) -> str | None:
    for item in datos.get("items") or []:
        intervalo = (((item or {}).get("price") or {}).get("billing_cycle") or {}).get("interval")
        if intervalo in CICLOS:
            return CICLOS[intervalo]
    return None


def _admite_ciclo(cliente: Any, ciclo: str) -> bool:
    """El ciclo es del plan de autoservicio. Un Custom se cobra con el
    intervalo que se haya armado en Paddle, y eso no es un ciclo nuestro."""
    return bool(planes.plan(db.valor(cliente, "plan")).get("ciclos"))


def _registrar(con, event_id, tipo, ocurrido, cliente_id, resultado, detalle,
               evento, crudo) -> dict[str, Any]:
    db.insertar(con, "eventos_pago", {
        "event_id": event_id,
        "tipo": tipo,
        "ocurrido_en": ocurrido.isoformat() if ocurrido else None,
        "cliente_id": cliente_id,
        "resultado": resultado,
        "detalle": detalle,
        "payload": crudo if crudo is not None else json.dumps(evento, ensure_ascii=False),
    })
    return {"resultado": resultado, "event_id": event_id, "cliente_id": cliente_id,
            "detalle": detalle}


def _fecha(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)
