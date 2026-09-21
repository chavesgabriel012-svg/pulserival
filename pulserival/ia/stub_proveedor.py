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
        cliente = datos.get("cliente", "cliente")
        periodo = f"{datos.get('periodo_inicio','')} al {datos.get('periodo_fin','')}"
        grupos = datos.get("anuncios") or []
        nuevos = [a for a in grupos if a.get("clasificacion") == "nuevo"]
        cambiados = [a for a in grupos if a.get("clasificacion") == "cambiado"]
        pausados = [a for a in grupos if a.get("clasificacion") == "pausado"]
        siguen = [a for a in grupos if a.get("clasificacion") == "continua"]

        L: list[str] = []
        L.append("## Resumen ejecutivo")
        L.append("")
        L.append(
            f"Entre el {periodo} detectamos {len(nuevos)} anuncio(s) nuevo(s), "
            f"{len(cambiados)} con cambios y {len(pausados)} que dejaron de aparecer, "
            f"entre los competidores que seguimos para {cliente}."
        )
        L.append("")

        def bloque(titulo: str, items: list[dict], vacio: str) -> None:
            L.append(f"## {titulo}")
            L.append("")
            if not items:
                L.append(f"_{vacio}_")
                L.append("")
                return
            for a in items:
                L.append(
                    f"- **{a.get('competidor')}** ({a.get('plataforma')}): "
                    f"{util.recortar(a.get('titulo') or a.get('texto'), 120)} {a.get('referencia','')}"
                )
            L.append("")

        L.append("## Panorama de la competencia")
        L.append("")
        L.append("_(borrador sin IA: no hay interpretación, solo el detalle)_")
        L.append("")
        L.append("## Qué está haciendo cada competidor")
        L.append("")
        L.append("_(borrador sin IA: abajo están los anuncios agrupados, sin interpretación)_")
        L.append("")
        bloque("Anuncios nuevos", nuevos, "No hubo anuncios nuevos en el periodo.")
        bloque("Cambios en anuncios que ya corrían", cambiados, "Nadie cambió sus anuncios activos.")
        bloque("Anuncios que dejaron de aparecer", pausados, "No se cayó ningún anuncio.")
        bloque("Movimientos que vale la pena mirar de cerca", siguen,
               "Sin anuncios sostenidos en el periodo.")

        L.append("## Qué haría yo esta semana")
        L.append("")
        L.append("_(borrador armado sin IA: revisá y escribí acá la recomendación)_")
        L.append("")
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
