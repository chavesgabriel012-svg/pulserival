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


_ultima_llamada = 0.0


def _respirar(proveedor: str) -> None:
    """Espacia las llamadas para no agotar el límite por minuto.

    Los tiers gratuitos de Groq y Gemini cortan con 429 a las pocas decenas
    de llamadas seguidas. Sin esta pausa, un reporte con muchos anuncios
    termina entero en el modo sin IA.

    El proveedor local no espera: no tiene límite de tasa y hacerlo esperar
    solo vuelve lentos los tests.
    """
    global _ultima_llamada
    if proveedor == "stub":
        return
    pausa = config.pausa_entre_llamadas()
    if pausa <= 0:
        return
    transcurrido = time.monotonic() - _ultima_llamada
    if transcurrido < pausa:
        time.sleep(pausa - transcurrido)
    _ultima_llamada = time.monotonic()


def _proveedor(nombre: str):
    from .gemini_proveedor import ProveedorGemini
    from .groq_proveedor import ProveedorGroq
    from .stub_proveedor import ProveedorStub

    return {"groq": ProveedorGroq, "gemini": ProveedorGemini, "stub": ProveedorStub}[nombre]()


def proveedores_configurados() -> dict[str, set[str]]:
    """Qué modelo pide config/modelos.yaml a cada proveedor, sin el stub.

    El stub no se lista: no es un servicio, es el respaldo escrito en el
    código y siempre está.
    """
    pedidos: dict[str, set[str]] = {}
    for lista in (config.config_modelos().get("tareas") or {}).values():
        for cand in lista or []:
            nombre = cand.get("proveedor", "stub")
            if nombre == "stub":
                continue
            pedidos.setdefault(nombre, set()).add(str(cand.get("modelo") or ""))
    return {n: {m for m in ms if m} for n, ms in pedidos.items()}


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

    def anotar_fallo(proveedor: str, modelo: str, detalle: str) -> None:
        """Deja registro de CADA intento fallido, no solo del fracaso total.

        Antes solo se registraba si fallaban todos los proveedores. Como el
        último candidato es 'stub' y nunca falla, un reporte podía salir sin
        IA —seco, sin interpretación— y no quedaba rastro de por qué: ni un
        429, ni una clave vencida, nada. Se descubrió justamente así, con un
        reporte real que salió en modo respaldo sin explicación.
        """
        errores.append(f"{proveedor}/{modelo}: {detalle}")
        if con is not None:
            db.registrar_uso_ia(
                con, tarea=peticion.tarea, proveedor=proveedor, modelo=modelo,
                exito=0, detalle=detalle[:500],
            )

    for cand in candidatos(peticion.tarea):
        nombre = cand.get("proveedor", "stub")
        modelo = cand.get("modelo", "stub")
        try:
            prov = _proveedor(nombre)
        except KeyError:
            anotar_fallo(nombre, modelo, "proveedor desconocido en config/modelos.yaml")
            continue
        if not prov.disponible():
            anotar_fallo(nombre, modelo, "sin clave configurada en el entorno")
            continue

        pet = Peticion(
            tarea=peticion.tarea,
            sistema=peticion.sistema,
            usuario=peticion.usuario,
            datos=peticion.datos,
            temperatura=float(cand.get("temperatura", peticion.temperatura)),
            max_tokens=int(cand.get("max_tokens", peticion.max_tokens)),
            json_estricto=peticion.json_estricto,
            nivel_razonamiento=cand.get("nivel_razonamiento"),
        )

        for intento in range(1, reintentos + 1):
            _respirar(nombre)
            try:
                resp = prov.generar(pet, modelo)
            except ProveedorError as e:
                anotar_fallo(nombre, modelo, f"intento {intento}: {e}")
                if intento < reintentos:
                    # Un 429 es el límite por minuto del tier gratuito: hay que
                    # esperar de verdad, no dos segundos.
                    # 429 = límite por minuto; 503 = el modelo está saturado.
                    # En ambos casos esperar poco no sirve de nada.
                    texto_error = str(e)
                    lento = "429" in texto_error or "503" in texto_error
                    espera = 20 * intento if lento else 2 * intento
                    time.sleep(espera)
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
    raise ProveedorError(f"Ningún proveedor de IA pudo atender '{peticion.tarea}': {detalle}")


def diagnostico(con: sqlite3.Connection | None = None) -> list[dict]:
    """Prueba cada proveedor configurado con una llamada mínima.

    Sirve para responder "¿por qué salió sin IA?" antes de gastar una
    corrida entera averiguándolo.
    """
    from .base import Peticion as _P

    vistos: dict[tuple[str, str], dict] = {}
    for tarea, lista in (config.config_modelos().get("tareas") or {}).items():
        for cand in lista:
            nombre, modelo = cand.get("proveedor", "stub"), cand.get("modelo", "stub")
            if (nombre, modelo) in vistos:
                vistos[(nombre, modelo)]["tareas"].append(tarea)
                continue
            fila = {"proveedor": nombre, "modelo": modelo, "tareas": [tarea]}
            try:
                prov = _proveedor(nombre)
            except KeyError:
                fila["estado"] = "desconocido"
                fila["detalle"] = "no existe ese proveedor en el código"
                vistos[(nombre, modelo)] = fila
                continue
            if not prov.disponible():
                fila["estado"] = "sin clave"
                fila["detalle"] = "la variable de entorno no está configurada"
                vistos[(nombre, modelo)] = fila
                continue
            try:
                r = prov.generar(_P(tarea="diagnostico", sistema="Responda solo: ok",
                                    usuario="ok", max_tokens=5), modelo)
                fila["estado"] = "ok"
                fila["detalle"] = f"respondió {r.tokens_salida or 0} tokens"
            except ProveedorError as e:
                fila["estado"] = "error"
                fila["detalle"] = str(e)[:300]
            vistos[(nombre, modelo)] = fila
    return list(vistos.values())
