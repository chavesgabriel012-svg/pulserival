"""Dónde queda un alta que llega del formulario.

Esto no es un detalle de implementación: define qué hosting sirve.

  - **sqlite**: el alta se escribe en la base, igual que cuando se carga por
    CLI. Necesita un disco que persista entre reinicios. Es lo que corre en
    la máquina local y en un servidor normal.

  - **github**: el alta se escribe como un archivo YAML en el repositorio, por
    la API de GitHub. No necesita disco: el proceso web puede ser efímero, que
    es la única forma de correr en Vercel. El cron que ya existe corre
    `aplicar-config`, que lee esos archivos y los carga a la base. La base
    sigue siendo una sola, la del cron; el servidor web nunca la toca.

La segunda opción existe porque en un hosting serverless el sistema de
archivos se borra entre invocaciones y cada instancia tiene el suyo: una base
SQLite ahí no guarda nada. En vez de cambiar la base por una en red —que
contradiría docs/01-decisiones-tecnicas.md y agregaría un servicio más—, el
alta se deposita donde ya vive la configuración de clientes.

Se elige con PULSERIVAL_DEPOSITO. Por defecto sqlite: nada de lo que ya
funcionaba cambia de comportamiento por existir este archivo.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import requests
import yaml

from .. import altas, config

# Adónde van los YAML de las altas dentro del repositorio. `sincronizar.leer_todo()`
# lee este directorio además de config/clientes.yaml.
DIR_ALTAS = "config/altas"
TIEMPO_ESPERA = 20


class DepositoError(RuntimeError):
    """El alta no se pudo guardar. El cliente tiene que enterarse, no perderse."""


class DepositoSqlite:
    """Escribe el alta en la base, como el CLI."""

    nombre = "sqlite"
    # Con base hay un id de cliente, así que el checkout simulado tiene algo
    # que confirmar. Sin base no hay nada que transicionar.
    tiene_checkout = True

    def __init__(self, conectar) -> None:
        self._conectar = conectar

    def pendientes(self) -> list[str]:
        # En Vercel el disco es efímero: la base se crea vacía en cada
        # instancia y el alta se pierde. Si alguien puso sqlite ahí a mano, no
        # se puede arreglar desde el código, pero sí se puede avisar por /salud
        # antes de que se pierda el primer alta.
        if config.env("VERCEL"):
            return ["PULSERIVAL_DEPOSITO=sqlite no funciona en Vercel: el disco es "
                    "efímero y el alta no se guarda. Poner 'github'."]
        return []

    def guardar(self, alta: dict[str, Any]) -> dict[str, Any]:
        con: sqlite3.Connection = self._conectar()
        try:
            with con:
                resultado = altas.crear(con, **alta)
        finally:
            con.close()
        return {**resultado, "referencia": str(resultado["cliente_id"])}


class DepositoGitHub:
    """Escribe el alta como un YAML nuevo en el repositorio.

    Un archivo por alta, nunca un append a config/clientes.yaml. Dos razones:
    reescribir ese archivo le borraría los comentarios, que son documentación
    de verdad; y dos altas simultáneas se pisarían (la API de GitHub pide el
    sha del archivo que se reemplaza). Con un archivo por alta no hay conflicto
    posible y queda un expediente por cliente.
    """

    nombre = "github"
    tiene_checkout = False

    def __init__(self, repo: str | None = None, token: str | None = None,
                 rama: str | None = None) -> None:
        self.repo = repo or config.env("PULSERIVAL_REPO") or ""
        self.token = token or config.env("PULSERIVAL_GITHUB_TOKEN") or ""
        self.rama = rama or config.env("PULSERIVAL_RAMA") or "main"

    def pendientes(self) -> list[str]:
        """Qué falta configurar. Se avisa al arrancar, no cuando llega el alta."""
        faltan = []
        if not self.repo:
            faltan.append("PULSERIVAL_REPO (formato usuario/repositorio)")
        if not self.token:
            faltan.append("PULSERIVAL_GITHUB_TOKEN (permiso de escritura en contenido)")
        return faltan

    def guardar(self, alta: dict[str, Any]) -> dict[str, Any]:
        if self.pendientes():
            raise DepositoError(
                "El servidor no tiene configurado dónde depositar las altas: falta "
                + ", ".join(self.pendientes()))

        listo = altas.preparar(**alta)
        clave = altas.clave_desde(listo["empresa"])
        ahora = datetime.now(timezone.utc)
        ruta = f"{DIR_ALTAS}/{ahora:%Y-%m-%d}-{clave}.yaml"
        cuerpo = _yaml_del_alta(listo, clave, ahora)

        respuesta = requests.put(
            f"https://api.github.com/repos/{self.repo}/contents/{ruta}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            data=json.dumps({
                "message": f"Alta desde la landing: {listo['empresa']} ({listo['plan']})",
                "content": base64.b64encode(cuerpo.encode("utf-8")).decode("ascii"),
                "branch": self.rama,
            }),
            timeout=TIEMPO_ESPERA,
        )
        if respuesta.status_code == 422:
            # Ya existe un archivo con ese nombre: la misma empresa se dio de
            # alta dos veces el mismo día. Se guarda al lado en vez de
            # sobreescribir: puede ser un dato corregido y el original importa.
            ruta = f"{DIR_ALTAS}/{ahora:%Y-%m-%d}-{clave}-{ahora:%H%M%S}.yaml"
            respuesta = requests.put(
                f"https://api.github.com/repos/{self.repo}/contents/{ruta}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                data=json.dumps({
                    "message": f"Alta desde la landing: {listo['empresa']} ({listo['plan']})",
                    "content": base64.b64encode(cuerpo.encode("utf-8")).decode("ascii"),
                    "branch": self.rama,
                }),
                timeout=TIEMPO_ESPERA,
            )
        if respuesta.status_code >= 400:
            raise DepositoError(
                f"GitHub respondió {respuesta.status_code} al guardar el alta: "
                f"{respuesta.text[:300]}")

        return {
            "cliente_id": None,
            "competidores": len(listo["competidores"]),
            "estado": listo["estado"],
            "referencia": ruta,
        }


def _yaml_del_alta(listo: dict[str, Any], clave: str, ahora: datetime) -> str:
    """El YAML que va al repositorio, con el encabezado que explica qué hacer.

    El encabezado no es decoración: este archivo lo va a leer una persona
    decidiendo si activa un cliente, y tiene que saber que activarlo cuesta
    plata de scraper.
    """
    entrada: dict[str, Any] = {
        "clave": clave,
        "empresa": listo["empresa"],
        "contacto_nombre": listo["contacto"],
        "contacto_email": listo["email"],
        "contacto_whatsapp": listo["whatsapp"],
        "plan": listo["plan"],
        "periodicidad": listo["periodicidad"],
        "estado_suscripcion": listo["estado"],
        "industria": listo["industria"],
        "notas": listo["notas"],
        # Las dos banderas que impiden que esto empiece a gastar solo. Hacen
        # falta las dos: `activo` lo saltea el pipeline entero, y
        # `estado_suscripcion` lo saltea la lógica de planes.
        "activo": False,
        "competidores": [
            {k: v for k, v in comp.items() if v is not None}
            for comp in listo["competidores"]
        ],
    }
    entrada = {k: v for k, v in entrada.items() if v is not None}
    cuerpo = yaml.safe_dump({"clientes": [entrada]}, allow_unicode=True, sort_keys=False,
                            default_flow_style=False, width=88)
    precio = listo.get("precio_usd")
    return (
        "# ─────────────────────────────────────────────────────────────────────\n"
        "#  Alta recibida por el formulario de la landing.\n"
        f"#  Fecha (UTC): {ahora:%Y-%m-%d %H:%M:%S}\n"
        f"#  Plan elegido: {listo['plan']}"
        + (f" (${precio:g} al mes)" if precio else " (sin precio fijo)") + "\n"
        "#\n"
        "#  NO está activa. `activo: false` y `estado_suscripcion` distinto de\n"
        "#  'activa' hacen que el ciclo la saltee, así que no gasta scraper ni IA.\n"
        "#\n"
        "#  Para activarla, después de confirmar el pago:\n"
        "#    1. revisar que los competidores tengan página de Facebook o dominio\n"
        "#       correctos (`cli prueba-scraper` confirma que hay anuncios);\n"
        "#    2. poner `activo: true` y `estado_suscripcion: activa` acá;\n"
        "#    3. correr `cli aplicar-config`.\n"
        "#\n"
        "#  Lo escribió el servidor web, no una persona: los datos son los que\n"
        "#  cargó el cliente y no están verificados.\n"
        "# ─────────────────────────────────────────────────────────────────────\n\n"
        + cuerpo
    )


def obtener(conectar, nombre: str | None = None):
    """El depósito que corresponda, según PULSERIVAL_DEPOSITO.

    El valor por defecto depende de dónde corre: en Vercel es 'github' porque
    sqlite ahí no guarda nada, y en cualquier otro lado es 'sqlite' para que lo
    que ya funcionaba siga igual. Se elige por el valor por defecto y no
    fallando: un servidor que no arranca no muestra ni la landing.
    """
    defecto = "github" if config.env("VERCEL") else "sqlite"
    nombre = (nombre or config.env("PULSERIVAL_DEPOSITO") or defecto).strip().lower()
    if nombre == "github":
        return DepositoGitHub()
    if nombre == "sqlite":
        return DepositoSqlite(conectar)
    raise DepositoError(
        f"PULSERIVAL_DEPOSITO='{nombre}' no existe. Las opciones son 'sqlite' y 'github'.")
