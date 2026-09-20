"""Proveedor Gemini (Google AI Studio).

Se usa para la tarea de calidad: redactar el reporte que lee el cliente.
Ojo con el crédito: el router estima el costo y corta si se pasa del tope.
"""
from __future__ import annotations

import requests

from .. import config
from .base import Peticion, ProveedorError, Respuesta

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class ProveedorGemini:
    nombre = "gemini"

    def __init__(self, clave: str | None = None):
        self.clave = clave or config.env("GEMINI_API_KEY")

    def disponible(self) -> bool:
        return bool(self.clave)

    def generar(self, peticion: Peticion, modelo: str) -> Respuesta:
        if not self.clave:
            raise ProveedorError("Falta GEMINI_API_KEY en .env (https://aistudio.google.com/apikey)")
        cuerpo = {
            "systemInstruction": {"parts": [{"text": peticion.sistema}]},
            "contents": [{"role": "user", "parts": [{"text": peticion.usuario}]}],
            "generationConfig": {
                "temperature": peticion.temperatura,
                "maxOutputTokens": peticion.max_tokens,
            },
        }
        if peticion.json_estricto:
            cuerpo["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(
                f"{BASE}/{modelo}:generateContent",
                headers={"x-goog-api-key": self.clave},
                json=cuerpo,
                timeout=180,
            )
        except requests.RequestException as e:
            raise ProveedorError(f"Gemini no respondió: {e}") from e
        if r.status_code == 429:
            raise ProveedorError("Gemini: límite de cuota alcanzado (429)")
        if r.status_code >= 400:
            raise ProveedorError(f"Gemini respondió {r.status_code}: {r.text[:300]}")
        datos = r.json()
        uso = datos.get("usageMetadata") or {}
        texto = ""
        for cand in datos.get("candidates") or []:
            for parte in (cand.get("content") or {}).get("parts") or []:
                texto += parte.get("text") or ""
        if not texto.strip():
            raise ProveedorError(f"Gemini devolvió texto vacío: {str(datos)[:300]}")
        return Respuesta(
            texto=texto,
            proveedor=self.nombre,
            modelo=modelo,
            tokens_entrada=int(uso.get("promptTokenCount") or 0),
            tokens_salida=int(uso.get("candidatesTokenCount") or 0),
        )
