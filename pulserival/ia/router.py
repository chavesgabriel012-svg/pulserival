"""Router de IA: decide qué modelo atiende cada tarea, estima el costo,
reintenta con el siguiente proveedor si el primero falla, y lo registra todo.

Cambiar de proveedor NO se hace acá: se hace en config/modelos.yaml.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from .. import config, db
from .base import Peticion, ProveedorError, Respuesta


class PresupuestoExcedido(RuntimeError):
    """Se alcanzó el tope de gasto de la corrida. Mejor cortar que quemar crédito."""


@dataclass
class Presupuesto:
    """Lleva la cuenta del gasto de una corrida y corta si se pasa del tope."""
    tope_usd: float = field(default_factory=config.tope_gasto_usd)
    gastado_usd: float = 0.0
    llamadas: int = 0

    def revisar(self) -> None:
        if self.gastado_usd >= self.tope_usd:
            raise PresupuestoExcedido(
                f"Gasto de IA en esta corrida: ${self.gastado_usd:.4f} "
                f"(tope ${self.tope_usd:.2f}). Subí el tope en config/modelos.yaml "
                "si de verdad querés seguir."
            )

    def sumar(self, respuesta: Respuesta) -> None:
        self.gastado_usd += respuesta.costo_usd
        self.llamadas += 1


def _proveedor(nombre: str):
    from .gemini_proveedor import ProveedorGemini
    from .groq_proveedor import ProveedorGroq
    from .stub_proveedor import ProveedorStub

    return {"groq": ProveedorGroq, "gemini": ProveedorGemini, "stub": ProveedorStub}[nombre]()


def costo(proveedor: str, modelo: str, tokens_entrada: int, tokens_salida: int) -> float:
    precios = config.config_modelos().get("precios_usd_por_millon") or {}
    clave = f"{proveedor}/{modelo.replace('/', '-')}"
    tarifa = precios.get(clave)
    if not tarifa:
        return 0.0
    return (
        tokens_entrada / 1_000_000 * float(tarifa.get("entrada", 0))
        + tokens_salida / 1_000_000 * float(tarifa.get("salida", 0))
    )


def candidatos(tarea: str) -> list[dict]:
    lista = (config.config_modelos().get("tareas") or {}).get(tarea)
    if not lista:
        return [{"proveedor": "stub", "modelo": "stub"}]
    return [dict(c) for c in lista]


def ejecutar(
    peticion: Peticion,
    con: sqlite3.Connection | None = None,
    presupuesto: Presupuesto | None = None,
    reintentos: int = 2,
) -> Respuesta:
    """Ejecuta la tarea con el primer proveedor que funcione.

    Orden: el de config/modelos.yaml. Si uno falla (sin clave, 429, red),
    se pasa al siguiente. El último de la lista suele ser 'stub', que nunca
    falla: así el pipeline siempre entrega algo revisable.
    """
    if presupuesto:
        presupuesto.revisar()

    errores: list[str] = []
    for cand in candidatos(peticion.tarea):
        nombre = cand.get("proveedor", "stub")
        modelo = cand.get("modelo", "stub")
        try:
            prov = _proveedor(nombre)
        except KeyError:
            errores.append(f"{nombre}: proveedor desconocido en modelos.yaml")
            continue
        if not prov.disponible():
            errores.append(f"{nombre}: sin clave configurada")
            continue

        pet = Peticion(
            tarea=peticion.tarea,
            sistema=peticion.sistema,
            usuario=peticion.usuario,
            datos=peticion.datos,
            temperatura=float(cand.get("temperatura", peticion.temperatura)),
            max_tokens=int(cand.get("max_tokens", peticion.max_tokens)),
            json_estricto=peticion.json_estricto,
        )

        for intento in range(1, reintentos + 1):
            try:
                resp = prov.generar(pet, modelo)
            except ProveedorError as e:
                errores.append(f"{nombre}/{modelo} (intento {intento}): {e}")
                if intento < reintentos:
                    time.sleep(2 * intento)
                continue
            resp.costo_usd = costo(resp.proveedor, resp.modelo, resp.tokens_entrada, resp.tokens_salida)
            if presupuesto:
                presupuesto.sumar(resp)
            if con is not None:
                db.registrar_uso_ia(
                    con,
                    tarea=peticion.tarea,
                    proveedor=resp.proveedor,
                    modelo=resp.modelo,
                    tokens_entrada=resp.tokens_entrada,
                    tokens_salida=resp.tokens_salida,
                    costo_usd=resp.costo_usd,
                    exito=1,
                    detalle=None,
                )
            return resp

    detalle = " | ".join(errores) or "sin proveedores configurados"
    if con is not None:
        db.registrar_uso_ia(
            con, tarea=peticion.tarea, proveedor="-", modelo="-", exito=0, detalle=detalle
        )
    raise ProveedorError(f"Ningún proveedor de IA pudo atender '{peticion.tarea}': {detalle}")
