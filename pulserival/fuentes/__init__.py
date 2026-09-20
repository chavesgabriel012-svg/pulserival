"""Fuentes de anuncios. Todas devuelven la misma estructura (AnuncioCrudo)."""
from .base import AnuncioCrudo, FuenteError, obtener_fuente

__all__ = ["AnuncioCrudo", "FuenteError", "obtener_fuente"]
