"""Proveedor Gemini (Google AI Studio).

Se usa para la tarea de calidad: redactar el reporte que lee el cliente.
Ojo con el crédito: el router estima el costo y corta si se pasa del tope.
"""
from __future__ import annotations

from typing import Any

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
        generacion: dict[str, Any] = {
            "temperature": peticion.temperatura,
            "maxOutputTokens": peticion.max_tokens,
        }
        # Los modelos Gemini 3.x razonan antes de responder, y esos tokens de
        # razonamiento salen del MISMO presupuesto de maxOutputTokens. Si se
        # agota mientras piensa, devuelve la respuesta cortada a media frase.
        # El nivel de razonamiento se puede bajar desde config/modelos.yaml.
        if peticion.nivel_razonamiento:
            generacion["thinkingLevel"] = peticion.nivel_razonamiento
        cuerpo = {
            "systemInstruction": {"parts": [{"text": peticion.sistema}]},
            "contents": [{"role": "user", "parts": [{"text": peticion.usuario}]}],
            "generationConfig": generacion,
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
        try:
            datos = r.json()
        except ValueError as e:
            # Un 200 que no es JSON (una página de error de un proxy, por
            # ejemplo) tiene que entrar en la cadena de respaldo como
            # cualquier otro fallo, no tumbar la corrida entera.
            raise ProveedorError(
                f"Gemini devolvió algo que no es JSON: {r.text[:200]}"
            ) from e
        uso = datos.get("usageMetadata") or {}
        texto = ""
        motivo = None
        for cand in datos.get("candidates") or []:
            motivo = cand.get("finishReason") or motivo
            for parte in (cand.get("content") or {}).get("parts") or []:
                texto += parte.get("text") or ""
        if not texto.strip():
            raise ProveedorError(
                f"Gemini devolvió texto vacío (finishReason={motivo}): {str(datos)[:200]}")
        # Una respuesta cortada NO se acepta. Pasó en un reporte real: el
        # modelo agotó el presupuesto razonando y el borrador terminó en
        # "Promociona un 20% de". Mejor que falle y entre el siguiente modelo
        # de la lista que entregarle media frase al cliente.
        if motivo and str(motivo).upper() not in ("STOP", "FINISH_REASON_STOP"):
            raise ProveedorError(
                f"Gemini cortó la respuesta (finishReason={motivo}) tras "
                f"{uso.get('candidatesTokenCount') or 0} tokens de texto y "
                f"{uso.get('thoughtsTokenCount') or 0} de razonamiento. "
                "Subí max_tokens o bajá nivel_razonamiento en config/modelos.yaml."
            )
        return Respuesta(
            texto=texto,
            proveedor=self.nombre,
            modelo=modelo,
            tokens_entrada=int(uso.get("promptTokenCount") or 0),
            # El razonamiento se factura como salida: si no se suma, el
            # registro de gasto queda por debajo de lo que se está pagando.
            tokens_salida=int(uso.get("candidatesTokenCount") or 0)
            + int(uso.get("thoughtsTokenCount") or 0),
        )
