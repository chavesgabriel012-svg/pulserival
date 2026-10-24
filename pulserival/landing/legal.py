"""Las páginas legales: Términos, Política de reembolso y Política de privacidad.

Los textos viven en config/legal.yaml. Acá solo se rellenan los datos del
responsable y los plazos, y se marca lo que falta en vez de inventarlo: una
razón social vacía sale en la página como "pendiente", a la vista, y
`cli landing` lo avisa. Paddle no aprueba el dominio sin esos datos, así que
publicar la página con un hueco disimulado no ahorraría nada.
"""
from __future__ import annotations

import re
from typing import Any

from .. import config

# clave del YAML -> (ruta en el servidor, archivo estático, texto del enlace)
PAGINAS = {
    "terminos": ("/terminos", "terminos.html", "Términos"),
    "reembolsos": ("/reembolsos", "reembolsos.html", "Reembolsos"),
    "privacidad": ("/privacidad", "privacidad.html", "Privacidad"),
}

# Lo que Paddle y la Ley 8968 piden que diga quién vende. El nombre comercial
# no está porque tiene valor por defecto.
OBLIGATORIOS = {
    "razon_social": "la razón social (nombre legal)",
    "identificacion": "la cédula física o jurídica",
    "direccion": "la dirección",
    "correo": "el correo de contacto",
}

_MARCA = re.compile(r"\{(\w+)\}")


def datos() -> dict[str, Any]:
    return dict(config.config_legal())


def valores() -> dict[str, str]:
    """Los reemplazos de los textos: el responsable y los números."""
    d = datos()
    salida = {k: str(v).strip() for k, v in (d.get("numeros") or {}).items()
              if v is not None}
    for k, v in (d.get("responsable") or {}).items():
        salida[k] = str(v or "").strip()
    salida.setdefault("nombre_comercial", "PulseRival")
    return salida


def faltantes() -> list[str]:
    v = valores()
    return [texto for clave, texto in OBLIGATORIOS.items() if not v.get(clave)]


def rellenar(texto: str | None, vals: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """El texto con sus marcas reemplazadas, en trozos para la plantilla.

    Cada trozo es {"texto": ..., "falta": bool}. La plantilla escapa el texto
    y pinta distinto los que faltan: armar el HTML acá obligaría a marcarlo
    como seguro a mano, que es como se cuela un texto sin escapar.

    Una marca sin valor queda visible como pendiente. Una marca que no existe
    se deja tal cual: es un error de tipeo en el YAML, y esconderlo haría que
    nadie lo vea.
    """
    vals = valores() if vals is None else vals
    trozos: list[dict[str, Any]] = []
    ultimo = 0
    texto = texto or ""
    for m in _MARCA.finditer(texto):
        trozos.append({"texto": texto[ultimo:m.start()], "falta": False})
        clave = m.group(1)
        if clave not in vals and clave not in OBLIGATORIOS:
            trozos.append({"texto": m.group(0), "falta": False})
        elif vals.get(clave):
            trozos.append({"texto": vals[clave], "falta": False})
        else:
            nombre = OBLIGATORIOS.get(clave, clave)
            trozos.append({"texto": f"[pendiente: {nombre}]", "falta": True})
        ultimo = m.end()
    trozos.append({"texto": texto[ultimo:], "falta": False})
    return [t for t in trozos if t["texto"]]


def plano(trozos: list[dict[str, Any]]) -> str:
    """Los trozos como texto plano (para el mensaje de WhatsApp, los tests)."""
    return "".join(t["texto"] for t in trozos)


def pagina(clave: str) -> dict[str, Any]:
    """Lo que la plantilla necesita para una de las tres páginas."""
    d = datos()
    vals = valores()
    crudo = d.get(clave) or {}
    secciones = []
    for s in crudo.get("secciones") or []:
        secciones.append({
            "titulo": rellenar(s.get("titulo"), vals),
            "parrafos": [rellenar(p, vals) for p in s.get("parrafos") or []],
            "lista": [rellenar(p, vals) for p in s.get("lista") or []],
            "encargados": bool(s.get("encargados")),
            "cierre": [rellenar(p, vals) for p in s.get("cierre") or []],
        })
    return {
        "clave": clave,
        "titulo": crudo.get("titulo") or PAGINAS[clave][2],
        "entrada": rellenar(crudo.get("entrada"), vals),
        "secciones": secciones,
        "encargados": [{"nombre": e.get("nombre", ""), "para": rellenar(e.get("para"), vals)}
                       for e in d.get("encargados") or []],
        "actualizado": d.get("actualizado") or "",
        "faltan": faltantes(),
    }


def version() -> str:
    """Qué versión de los textos aceptó un cliente: la fecha de actualización."""
    return str(datos().get("actualizado") or "")


def enlaces(estatico: bool) -> dict[str, str]:
    """La URL de cada página, según se sirva desde Flask o desde una carpeta."""
    return {clave: (archivo if estatico else ruta)
            for clave, (ruta, archivo, _) in PAGINAS.items()}
