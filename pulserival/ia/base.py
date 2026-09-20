"""Contrato común de los proveedores de IA.

Regla de diseño: ningún archivo del proyecto menciona "Groq" o "Gemini" salvo
los archivos de proveedor y el YAML de configuración. Así, cuando cambien los
precios o salga un modelo mejor, se cambia una línea de YAML.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProveedorError(RuntimeError):
    """El proveedor falló: sin clave, sin cuota, error de red, respuesta rara."""


@dataclass
class Peticion:
    tarea: str                       # 'analizar_anuncio' | 'redactar_reporte' | ...
    sistema: str
    usuario: str
    datos: dict[str, Any] = field(default_factory=dict)  # insumo estructurado
    temperatura: float = 0.2
    max_tokens: int = 1200
    json_estricto: bool = False      # pedir salida JSON


@dataclass
class Respuesta:
    texto: str
    proveedor: str
    modelo: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    costo_usd: float = 0.0

    def json(self, defecto: Any = None) -> Any:
        """Lee JSON aunque el modelo lo devuelva envuelto en ```json ... ```."""
        return extraer_json(self.texto, defecto)


class Proveedor(Protocol):
    nombre: str

    def disponible(self) -> bool:
        ...

    def generar(self, peticion: Peticion, modelo: str) -> Respuesta:
        ...


_BLOQUE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extraer_json(texto: str, defecto: Any = None) -> Any:
    if not texto:
        return defecto
    candidatos = [texto]
    m = _BLOQUE.search(texto)
    if m:
        candidatos.insert(0, m.group(1))
    # último recurso: el primer {...} o [...] balanceado a ojo
    for abre, cierra in (("{", "}"), ("[", "]")):
        i, j = texto.find(abre), texto.rfind(cierra)
        if i != -1 and j > i:
            candidatos.append(texto[i : j + 1])
    for c in candidatos:
        try:
            return json.loads(c.strip())
        except (ValueError, TypeError):
            continue
    return defecto
