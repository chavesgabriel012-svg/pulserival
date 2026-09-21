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
URL_MODELOS = "https://api.groq.com/openai/v1/models"


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
        try:
            datos = r.json()
        except ValueError as e:
            # Un 200 que no es JSON (una página de error de un proxy, por
            # ejemplo) tiene que entrar en la cadena de respaldo como
            # cualquier otro fallo, no tumbar la corrida entera.
            raise ProveedorError(
                f"Groq devolvió algo que no es JSON: {r.text[:200]}"
            ) from e
        uso = datos.get("usage") or {}
        try:
            eleccion = datos["choices"][0]
            texto = eleccion["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise ProveedorError(f"Respuesta inesperada de Groq: {str(datos)[:300]}") from e
        # Una respuesta cortada no se acepta: mejor pasar al siguiente modelo.
        if eleccion.get("finish_reason") == "length":
            raise ProveedorError(
                f"Groq cortó la respuesta por límite de tokens tras "
                f"{uso.get('completion_tokens') or 0}. Subí max_tokens en config/modelos.yaml.")
        return Respuesta(
            texto=texto,
            proveedor=self.nombre,
            modelo=modelo,
            tokens_entrada=int(uso.get("prompt_tokens") or 0),
            tokens_salida=int(uso.get("completion_tokens") or 0),
        )

    def listar_modelos(self) -> list[str]:
        """Los nombres que esta llave puede usar HOY.

        Groq retira modelos sin avisar: `llama-3.3-70b-versatile` empezó a
        devolver 404 "does not exist or you do not have access to it" con el
        YAML sin tocar. Esto evita adivinar nombres.
        """
        if not self.clave:
            raise ProveedorError("Falta GROQ_API_KEY en .env")
        try:
            r = requests.get(
                URL_MODELOS,
                headers={"Authorization": f"Bearer {self.clave}"},
                timeout=60,
            )
        except requests.RequestException as e:
            raise ProveedorError(f"Groq no respondió: {e}") from e
        if r.status_code >= 400:
            raise ProveedorError(f"Groq respondió {r.status_code}: {r.text[:300]}")
        try:
            datos = r.json()
        except ValueError as e:
            raise ProveedorError(f"Groq devolvió algo que no es JSON: {r.text[:200]}") from e
        return sorted(str(m.get("id")) for m in (datos.get("data") or []) if m.get("id"))
