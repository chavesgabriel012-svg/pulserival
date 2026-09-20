"""Fuente de demostración: datos de ejemplo, sin internet ni costo.

Sirve para (a) probar todo el pipeline antes de pagar un solo colón, y
(b) correr los tests. Los datos son ficticios: un gimnasio en San José y dos
competidores inventados.

La variable de entorno PULSERIVAL_DEMO_SEMANA controla qué "semana" devuelve:
  1 -> primera corrida
  2 -> segunda corrida: hay anuncios nuevos, uno cambió de oferta y uno se cayó.
Así podés ver funcionando la detección de cambios sin esperar una semana.
"""
from __future__ import annotations

import os

from .base import AnuncioCrudo

_META_SEMANA_1 = [
    dict(
        id_externo="demo-meta-001",
        titulo="Matrícula GRATIS en setiembre",
        texto="Arrancá hoy sin pagar matrícula. Plan mensual ¢19.900 con acceso a todas "
              "las clases grupales. Sucursales en Escazú y Curridabat. Cupos limitados.",
        descripcion="Sin contrato de 12 meses",
        cta="Registrarte",
        link_destino="https://vitalgymcr.example.com/promo-setiembre?utm_source=fb",
        creativo_url="https://cdn.example.com/vital/matricula-gratis.jpg",
        tipo_creativo="imagen",
        fecha_inicio="2026-09-01",
    ),
    dict(
        id_externo="demo-meta-002",
        titulo="Rutina de 30 minutos para gente ocupada",
        texto="No tenés tiempo, tenés 30 minutos. Entrenamiento guiado para quienes "
              "trabajan de 8 a 5. Primera clase de prueba sin costo.",
        cta="Más información",
        link_destino="https://vitalgymcr.example.com/clase-prueba",
        creativo_url="https://cdn.example.com/vital/30min.mp4",
        tipo_creativo="video",
        fecha_inicio="2026-08-25",
    ),
    dict(
        id_externo="demo-meta-101",
        grupo="b",
        titulo="Membresía familiar: 2 adultos + 2 niños",
        texto="Un solo pago mensual para toda la familia. Piscina, zona infantil y "
              "parqueo incluido. Escazú, frente al parque.",
        cta="Escribinos",
        link_destino="https://clubatlas.example.com/familiar",
        creativo_url="https://cdn.example.com/atlas/familiar.jpg",
        tipo_creativo="imagen",
        fecha_inicio="2026-08-10",
    ),
]

_META_SEMANA_2 = [
    # mismo anuncio 001 pero cambió el precio -> se detecta como CAMBIADO
    dict(
        id_externo="demo-meta-001",
        titulo="Matrícula GRATIS en setiembre",
        texto="Arrancá hoy sin pagar matrícula. Plan mensual ¢16.900 con acceso a todas "
              "las clases grupales. Sucursales en Escazú y Curridabat. Últimos días.",
        descripcion="Sin contrato de 12 meses",
        cta="Registrarte",
        link_destino="https://vitalgymcr.example.com/promo-setiembre?utm_source=fb",
        creativo_url="https://cdn.example.com/vital/matricula-gratis.jpg",
        tipo_creativo="imagen",
        fecha_inicio="2026-09-01",
    ),
    # 002 desaparece -> se detecta como PAUSADO
    dict(
        id_externo="demo-meta-003",
        titulo="Nuevo: entrenamiento personalizado desde ¢12.000 la sesión",
        texto="Un entrenador solo para vos, 3 veces por semana. Evaluación física "
              "incluida. Disponible en Curridabat.",
        cta="Reservar",
        link_destino="https://vitalgymcr.example.com/personal",
        creativo_url="https://cdn.example.com/vital/personal.jpg",
        tipo_creativo="imagen",
        fecha_inicio="2026-09-15",
    ),
    dict(
        id_externo="demo-meta-101",
        grupo="b",
        titulo="Membresía familiar: 2 adultos + 2 niños",
        texto="Un solo pago mensual para toda la familia. Piscina, zona infantil y "
              "parqueo incluido. Escazú, frente al parque.",
        cta="Escribinos",
        link_destino="https://clubatlas.example.com/familiar",
        creativo_url="https://cdn.example.com/atlas/familiar.jpg",
        tipo_creativo="imagen",
        fecha_inicio="2026-08-10",
    ),
    dict(
        id_externo="demo-meta-102",
        grupo="b",
        titulo="Clases de natación para niños",
        texto="Grupos por edad, instructores certificados, matrícula abierta para "
              "el bloque de octubre.",
        cta="Más información",
        link_destino="https://clubatlas.example.com/natacion",
        creativo_url="https://cdn.example.com/atlas/natacion.jpg",
        tipo_creativo="imagen",
        fecha_inicio="2026-09-16",
    ),
]

_GOOGLE_SEMANA_1 = [
    dict(
        id_externo="demo-goog-001",
        titulo="Gimnasio en Escazú | Matrícula gratis",
        texto="Plan mensual sin contrato. Clases grupales incluidas. Reservá tu visita hoy.",
        link_destino="https://vitalgymcr.example.com/",
        tipo_creativo="texto",
        fecha_inicio="2026-08-20",
    ),
]

_GOOGLE_SEMANA_2 = [
    dict(
        id_externo="demo-goog-001",
        titulo="Gimnasio en Escazú | Matrícula gratis",
        texto="Plan mensual sin contrato. Clases grupales incluidas. Reservá tu visita hoy.",
        link_destino="https://vitalgymcr.example.com/",
        tipo_creativo="texto",
        fecha_inicio="2026-08-20",
    ),
    dict(
        id_externo="demo-goog-002",
        titulo="Entrenador personal en Curridabat | Desde ¢12.000",
        texto="Sesiones 1 a 1 con evaluación física incluida. Agendá esta semana.",
        link_destino="https://vitalgymcr.example.com/personal",
        tipo_creativo="texto",
        fecha_inicio="2026-09-15",
    ),
]

_DATOS = {
    ("meta", 1): _META_SEMANA_1,
    ("meta", 2): _META_SEMANA_2,
    ("google", 1): _GOOGLE_SEMANA_1,
    ("google", 2): _GOOGLE_SEMANA_2,
}


class FuenteDemo:
    def __init__(self, plataforma: str, aviso: bool = False):
        self.plataforma = plataforma
        self.nombre = f"demo:{plataforma}"
        self.aviso = aviso

    def traer(self, competidor: dict, limite: int = 40) -> list[AnuncioCrudo]:
        semana = int(os.environ.get("PULSERIVAL_DEMO_SEMANA", "1"))
        # Hay dos juegos de anuncios de ejemplo ("a" y "b"). Cada competidor
        # recibe uno, de forma estable, para que no se mezclen entre ellos.
        grupo_comp = "b" if int(competidor.get("id") or 1) % 2 == 0 else "a"
        salida = []
        for item in _DATOS.get((self.plataforma, semana), []):
            item = dict(item)
            if item.pop("grupo", "a") != grupo_comp:
                continue
            if len(salida) >= limite:
                break
            anunciante = competidor.get("nombre")
            salida.append(
                AnuncioCrudo(
                    plataforma=self.plataforma,
                    fuente=self.nombre,
                    anunciante=anunciante,
                    url_anuncio=f"https://www.facebook.com/ads/library/?id={item.get('id_externo')}"
                    if self.plataforma == "meta"
                    else f"https://adstransparency.google.com/advertiser/demo/creative/{item.get('id_externo')}",
                    metadata={"demo": True, "semana": semana},
                    **item,
                )
            )
        return salida
