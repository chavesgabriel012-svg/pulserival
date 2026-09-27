"""Distribución del reporte final."""
from .email import EnvioError, enviar_reporte, previsualizar

__all__ = ["enviar_reporte", "previsualizar", "EnvioError"]
