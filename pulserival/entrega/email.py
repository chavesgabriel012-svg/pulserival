"""Paso 4 del pipeline: DISTRIBUIR.

Tres modos, en orden de preferencia:
  1. Resend (API HTTP). Es el más simple de operar: una clave, un dominio
     verificado, y reportes de entrega/bounce en el panel.
  2. SMTP (Gmail, Zoho, cualquiera). Si ya tenés correo corporativo y no
     querés otro servicio.
  3. Borrador local (`--simular`). Escribe el HTML en salida/ y no manda nada.
     Es el modo por defecto mientras no haya claves: nunca se le escribe a un
     cliente por accidente.
"""
from __future__ import annotations

import smtplib
import sqlite3
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

from .. import config, db, util
from ..reporte import render

URL_RESEND = "https://api.resend.com/emails"


class EnvioError(RuntimeError):
    pass


def _armar(con: sqlite3.Connection, reporte_id: int) -> dict[str, Any]:
    rep = db.fila(
        con,
        "SELECT r.*, c.nombre_empresa, c.contacto_email, c.contacto_nombre, c.contacto_whatsapp "
        "FROM reportes_generados r JOIN clientes c ON c.id = r.cliente_id WHERE r.id = ?",
        (reporte_id,),
    )
    if not rep:
        raise EnvioError(f"No existe el reporte {reporte_id}")
    cuerpo = rep["final_md"] or rep["borrador_md"]
    if not cuerpo:
        raise EnvioError("El reporte no tiene contenido.")
    datos = db.leer_json(rep["datos_json"], {}) or {}
    return {
        "reporte_id": reporte_id,
        "estado": rep["estado"],
        "cliente": rep["nombre_empresa"],
        "contacto_email": rep["contacto_email"],
        "contacto_nombre": rep["contacto_nombre"],
        "contacto_whatsapp": rep["contacto_whatsapp"],
        "asunto": rep["asunto"] or "Reporte de anuncios de la competencia",
        "preheader": util.recortar(cuerpo.replace("#", "").strip(), 110),
        "periodo_inicio": rep["periodo_inicio"],
        "periodo_fin": rep["periodo_fin"],
        "conteo": datos.get("conteo"),
        "senales": datos.get("senales") or [],
        "competidores": datos.get("competidores") or [],
        "anuncios": datos.get("anuncios") or [],
        "marca": config.env("MARCA_REPORTE") or "PulseRival",
        "contacto_remitente": config.env("EMAIL_RESPONDER_A"),
        # Las miniaturas se sirven desde los CDN de Meta y Google. Se pueden
        # apagar si algún cliente de correo las bloquea.
        "miniaturas": (config.env("REPORTE_MINIATURAS") or "1") != "0",
        "cuerpo_md": cuerpo,
        "es_borrador": rep["estado"] == "borrador",
    }


def enviar_reporte(
    con: sqlite3.Connection,
    reporte_id: int,
    simular: bool = False,
    destinatario: str | None = None,
    permitir_borrador: bool = False,
) -> dict[str, Any]:
    """Envía el reporte final. Por seguridad: si el reporte todavía está en
    estado 'borrador' (no pasó por tu revisión), no se envía salvo que lo pidas
    explícitamente con permitir_borrador=True."""
    rep = _armar(con, reporte_id)
    if rep["es_borrador"] and not permitir_borrador and not simular:
        raise EnvioError(
            f"El reporte {reporte_id} todavía está en estado 'borrador'. "
            "Registrá tu versión final primero (reporte registrar), o usá --forzar "
            "si de verdad querés enviar el borrador tal cual."
        )
    if rep["estado"] == "enviado":
        raise EnvioError(f"El reporte {reporte_id} ya fue enviado ({rep['cliente']}). No se manda dos veces.")

    html = render.email_html(rep)
    texto = render.email_texto(rep)
    para = destinatario or rep["contacto_email"]
    if not para:
        raise EnvioError("El cliente no tiene correo de contacto.")

    # copia local siempre, haya o no envío: queda el respaldo de lo que se mandó
    archivo = _guardar_copia(rep, html, texto)

    if simular:
        return {"canal": "simulado", "archivo": str(archivo), "para": para, "enviado": False}

    remitente = config.env("EMAIL_REMITENTE") or "PulseRival <reportes@example.com>"
    if config.env("RESEND_API_KEY"):
        resultado = _via_resend(para, remitente, rep["asunto"], html, texto)
        canal = "email:resend"
    elif config.env("SMTP_HOST"):
        resultado = _via_smtp(para, remitente, rep["asunto"], html, texto)
        canal = "email:smtp"
    else:
        raise EnvioError(
            "No hay forma de enviar configurada. Poné RESEND_API_KEY o SMTP_HOST en .env, "
            "o usá --simular para generar el archivo sin enviar."
        )

    db.actualizar(con, "reportes_generados", reporte_id,
                  {"estado": "enviado", "enviado_en": util.ahora_iso(), "canal_envio": canal})
    return {"canal": canal, "archivo": str(archivo), "para": para, "enviado": True, **resultado}


def _guardar_copia(rep: dict, html: str, texto: str) -> Path:
    carpeta = config.DIR_SALIDA
    carpeta.mkdir(parents=True, exist_ok=True)
    base = f"{rep['periodo_fin']}-reporte-{rep['reporte_id']}"
    (carpeta / f"{base}.html").write_text(html, encoding="utf-8")
    (carpeta / f"{base}.txt").write_text(texto, encoding="utf-8")
    (carpeta / f"{base}-whatsapp.txt").write_text(render.whatsapp(rep), encoding="utf-8")
    return carpeta / f"{base}.html"


def _via_resend(para: str, remitente: str, asunto: str, html: str, texto: str) -> dict:
    cuerpo = {"from": remitente, "to": [para], "subject": asunto, "html": html, "text": texto}
    responder_a = config.env("EMAIL_RESPONDER_A")
    if responder_a:
        cuerpo["reply_to"] = responder_a
    try:
        r = requests.post(
            URL_RESEND,
            headers={"Authorization": f"Bearer {config.env('RESEND_API_KEY')}"},
            json=cuerpo,
            timeout=60,
        )
    except requests.RequestException as e:
        raise EnvioError(f"Resend no respondió: {e}") from e
    if r.status_code >= 400:
        raise EnvioError(f"Resend respondió {r.status_code}: {r.text[:300]}")
    return {"id_proveedor": (r.json() or {}).get("id")}


def _via_smtp(para: str, remitente: str, asunto: str, html: str, texto: str) -> dict:
    msg = EmailMessage()
    msg["From"] = remitente
    msg["To"] = para
    msg["Subject"] = asunto
    responder_a = config.env("EMAIL_RESPONDER_A")
    if responder_a:
        msg["Reply-To"] = responder_a
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    host = config.env("SMTP_HOST")
    puerto = int(config.env("SMTP_PUERTO") or 587)
    try:
        with smtplib.SMTP(host, puerto, timeout=60) as s:
            s.starttls()
            usuario, clave = config.env("SMTP_USUARIO"), config.env("SMTP_CLAVE")
            if usuario and clave:
                s.login(usuario, clave)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise EnvioError(f"Falló el envío por SMTP ({host}:{puerto}): {e}") from e
    return {"id_proveedor": None}
