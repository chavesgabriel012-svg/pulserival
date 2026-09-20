"""Proveedor Groq (API compatible con OpenAI).

Se usa para las tareas de alto volumen: leer y clasificar cada anuncio.
Tiene tier gratuito con límites por minuto, así que el router reintenta y,
si se agota, cae al siguiente proveedor de la lista.
"""
from __future__ import annotations

import requests

from .. import config
from .base import Peticion, ProveedorError, Respuesta

URL = "https://api.groq.com/openai/v1/chat/completions"


class ProveedorGroq:
    nombre = "groq"

    def __init__(self, clave: str | None = None):
        self.clave = clave or config.env("GROQ_API_KEY")

    def disponible(self) -> bool:
        return bool(self.clave)

    def generar(self, peticion: Peticion, modelo: str) -> Respuesta:
        if not self.clave:
            raise ProveedorError("Falta GROQ_API_KEY en .env (https://console.groq.com/keys)")
        cuerpo = {
            "model": modelo,
            "messages": [
                {"role": "system", "content": peticion.sistema},
                {"role": "user", "content": peticion.usuario},
            ],
            "temperature": peticion.temperatura,
            "max_tokens": peticion.max_tokens,
        }
        if peticion.json_estricto:
            cuerpo["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(
                URL,
                headers={"Authorization": f"Bearer {self.clave}"},
                json=cuerpo,
                timeout=120,
            )
        except requests.RequestException as e:
            raise ProveedorError(f"Groq no respondió: {e}") from e
        if r.status_code == 429:
            raise ProveedorError("Groq: se agotó el límite por minuto del tier gratuito (429)")
        if r.status_code >= 400:
            raise ProveedorError(f"Groq respondió {r.status_code}: {r.text[:300]}")
        datos = r.json()
        uso = datos.get("usage") or {}
        try:
            texto = datos["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise ProveedorError(f"Respuesta inesperada de Groq: {str(datos)[:300]}") from e
        return Respuesta(
            texto=texto,
            proveedor=self.nombre,
            modelo=modelo,
            tokens_entrada=int(uso.get("prompt_tokens") or 0),
            tokens_salida=int(uso.get("completion_tokens") or 0),
        )
