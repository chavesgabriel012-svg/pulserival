"""La landing pública: planes, precios y el formulario de alta.

El módulo se llama `generador` y no `construir` a propósito: si el módulo y
la función se llaman igual, el paquete tapa al submódulo y
`import pulserival.landing.construir` devuelve la función en vez del módulo.
"""
from .generador import construir, pendientes

__all__ = ["construir", "pendientes"]
