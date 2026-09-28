"""La identidad de marca: logos, favicons e imagen para compartir.

Los archivos de `activos/` vienen de la entrega de diseño y son definitivos.
Los SVG son la fuente; los PNG se derivan de ellos (ver scripts del commit que
los introdujo), así que no pueden quedar desalineados con el vector.

Se sirven bajo /marca/ en vez de la raíz del sitio a propósito: un solo lugar,
una sola regla de caché, y nada que se pise con las rutas de la aplicación.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

ACTIVOS = Path(__file__).parent / "activos"

# Los colores de la marca, para el código que necesita uno suelto (el correo,
# que no puede usar variables CSS). La fuente es assets/tokens.json de la
# entrega; acá están los que el código usa de verdad.
TINTA = "#141312"
PAPEL = "#F4F2EE"
BLANCO = "#FFFFFF"
PULSO = "#DD4115"
PULSO_OSCURO = "#EF5B36"
PULSO_TEXTO = "#B22800"
PIEDRA = "#8A857D"
GRIS_TEXTO = "#6B665E"
LINEA = "#D9D5CE"
ESCRITORIO = "#1E1D1B"
LINEA_OSCURA = "#3A3834"
TEXTO_OSCURO_2 = "#B5B0A6"


@lru_cache(maxsize=None)
def data_uri(archivo: str) -> str:
    """Un activo como data URI. Para el correo, que no puede pedir archivos."""
    ruta = ACTIVOS / archivo
    if not ruta.exists():
        return ""
    tipo = "image/svg+xml" if ruta.suffix == ".svg" else "image/png"
    return f"data:{tipo};base64," + base64.b64encode(ruta.read_bytes()).decode("ascii")


def existe(archivo: str) -> bool:
    return (ACTIVOS / archivo).is_file()


# La paleta completa, en minúsculas y sin el numeral, para comprobarla contra
# lo que sale renderizado. Existe para que haya UNA lista: si alguien agrega
# un color a mano en una plantilla, los tests lo cazan.
#
# Reemplaza a la regla vieja de "todo gris": la identidad de marca trae el
# naranja Pulso, así que prohibir el color dejó de tener sentido. Lo que sí
# sigue teniendo sentido es prohibir un color que nadie decidió.
PALETA = {
    TINTA, PAPEL, BLANCO, PULSO, PULSO_OSCURO, PULSO_TEXTO, PIEDRA,
    GRIS_TEXTO, LINEA, ESCRITORIO, LINEA_OSCURA, TEXTO_OSCURO_2,
    "#E4E1DA",   # línea suave del correo, derivada de --linea
}
_PALETA_NORMALIZADA = {c.lower() for c in PALETA}


def fuera_de_paleta(texto: str) -> list[str]:
    """Los colores hexadecimales del texto que no son de la marca."""
    import re

    usados = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}", texto)}
    return sorted(usados - _PALETA_NORMALIZADA)
