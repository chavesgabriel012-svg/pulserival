"""Proveedor 'stub': sin IA, sin internet, sin costo.

Tres razones para que exista:
  1. Podés probar todo el sistema antes de gastar un colón o pedir una clave.
  2. Los tests corren sin red (y por eso son confiables).
  3. Es la última red de seguridad: si Groq y Gemini fallan un martes a las
     6 a.m., el pipeline igual produce un borrador armado con reglas simples,
     y vos lo editás. Nunca te quedás sin nada que revisar.

Lo que produce es correcto pero soso: describe los datos sin interpretarlos.
Eso está bien: el borrador dice arriba con qué modelo se generó.
"""
from __future__ import annotations

import re

from .. import util
from .base import Peticion, Respuesta

PRECIO_RE = re.compile(r"(?:¢|₡|\$|CRC\s?)\s?[\d.,]+\s?(?:mil|colones)?", re.I)
PALABRAS_OFERTA = [
    "gratis", "descuento", "promo", "oferta", "2x1", "sin costo", "regalo",
    "matrícula", "%", "rebaja", "liquidación", "cupón", "envío gratis",
]
PALABRAS_URGENCIA = ["hoy", "últimos", "ultimos", "cupos", "limitado", "termina", "solo por"]


class ProveedorStub:
    nombre = "stub"

    def disponible(self) -> bool:
        return True

    def generar(self, peticion: Peticion, modelo: str = "stub") -> Respuesta:
        if peticion.tarea == "analizar_anuncio":
            texto = self._analizar(peticion.datos)
        elif peticion.tarea == "redactar_reporte":
            texto = self._redactar(peticion.datos)
        elif peticion.tarea == "etiquetar_edicion":
            texto = self._etiquetar(peticion.datos)
        else:
            texto = ""
        return Respuesta(texto=texto, proveedor=self.nombre, modelo="stub")

    # ── análisis de un anuncio, con reglas ───────────────────────────
    def _analizar(self, datos: dict) -> str:
        import json

        texto = " ".join(
            str(datos.get(k) or "") for k in ("titulo", "texto", "descripcion", "cta")
        )
        plano = util.normalizar_texto(texto)
        precios = [m.strip() for m in PRECIO_RE.findall(texto)]
        # `plano` viene sin acentos: la palabra buscada también tiene que ir
        # normalizada, o "matrícula" y "liquidación" nunca calzan.
        oferta = [p for p in PALABRAS_OFERTA if util.normalizar_texto(p) in plano]
        urgencia = [p for p in PALABRAS_URGENCIA if util.normalizar_texto(p) in plano]
        return json.dumps(
            {
                "angulo": util.recortar(datos.get("titulo") or texto, 90) or "sin texto",
                "promesa": util.recortar(texto, 140),
                "tipo_oferta": "promocion" if oferta else ("precio" if precios else "marca"),
                "precios_mencionados": precios[:3],
                "publico_probable": "general",
                "formato": datos.get("tipo_creativo") or "texto",
                "usa_urgencia": bool(urgencia),
                "senales": sorted(set(oferta + urgencia))[:6],
                "generado_por": "reglas",
            },
            ensure_ascii=False,
        )

    # ── borrador de reporte, con plantilla ───────────────────────────
    def _redactar(self, datos: dict) -> str:
        """Borrador de respaldo, sin interpretación.

        No repite la lista de anuncios: el correo ya la trae al final,
        agrupada por competidor. Acá solo van los conteos, para que el
        editor tenga sobre qué escribir.
        """
        cliente = datos.get("cliente", "el cliente")
        anuncios = datos.get("anuncios") or []
        por_clase = {}
        for a in anuncios:
            por_clase.setdefault(a.get("clasificacion"), []).append(a)
        competidores = sorted({a.get("competidor") for a in anuncios if a.get("competidor")})

        L: list[str] = ["## Resumen ejecutivo", ""]
        L.append(
            f"Entre el {datos.get('periodo_inicio','')} y el {datos.get('periodo_fin','')} se "
            f"detectaron {len(por_clase.get('nuevo', []))} anuncios nuevos, "
            f"{len(por_clase.get('cambiado', []))} con cambios y "
            f"{len(por_clase.get('pausado', []))} que dejaron de publicarse, entre los "
            f"competidores que seguimos para {cliente}."
        )
        L += ["", "_(borrador generado sin IA: los conteos son correctos, la "
              "interpretación queda pendiente de revisión)_", ""]

        L += ["## Panorama de la competencia", ""]
        for nombre in competidores:
            propios = [a for a in anuncios if a.get("competidor") == nombre]
            nuevos = sum(1 for a in propios if a.get("clasificacion") == "nuevo")
            L.append(f"- {nombre}: {len(propios)} anuncios detectados, {nuevos} nuevos "
                     "en el periodo.")
        if not competidores:
            L.append("Sin actividad detectada en el periodo.")
        L.append("")

        L += ["## Qué está haciendo cada competidor", ""]
        for nombre in competidores:
            L.append(f"### {nombre}")
            L.append("")
            for plataforma, etiqueta in (("meta", "Meta (Facebook e Instagram)"),
                                         ("google", "Google")):
                propios = [a for a in anuncios
                           if a.get("competidor") == nombre and a.get("plataforma") == plataforma]
                if not propios:
                    continue
                sin_texto = sum(1 for a in propios if a.get("sin_texto"))
                detalle = f"{len(propios)} anuncios detectados"
                if sin_texto:
                    detalle += f", {sin_texto} sin texto publicado por la plataforma"
                L += [f"**{etiqueta}** — {detalle}.", ""]
        L += ["## Movimientos que vale la pena mirar de cerca", "",
              "_(pendiente de revisión)_", "",
              "## Qué haría yo esta semana", "",
              "_(pendiente de revisión)_", ""]
        return "\n".join(L)

    def _etiquetar(self, datos: dict) -> str:
        import json

        diff = datos.get("diff") or ""
        agregadas = diff.count("\n+")
        quitadas = diff.count("\n-")
        if agregadas and not quitadas:
            etiqueta = "agregue_contexto"
        elif quitadas and not agregadas:
            etiqueta = "recorte"
        else:
            etiqueta = "reescritura"
        return json.dumps({"etiqueta": etiqueta, "razon": ""}, ensure_ascii=False)
