"""Capa de IA. El resto del sistema nunca llama a Groq ni a Gemini directo:
pasa por `ejecutar()`, que decide el modelo según config/modelos.yaml.
"""
from .base import Peticion, Respuesta, ProveedorError
from .router import ejecutar, Presupuesto, PresupuestoExcedido

__all__ = [
    "Peticion", "Respuesta", "ProveedorError",
    "ejecutar", "Presupuesto", "PresupuestoExcedido",
]
