"""Las tareas largas que se disparan desde el panel, en segundo plano.

Buscar competidores, recolectar o rehacer un reporte tardan de segundos a
varios minutos (Apify espera hasta diez). Una petición web no puede esperar
tanto: gunicorn la corta a los 120 segundos. Por eso corren en un hilo, y la
página del cliente muestra cómo van.

Una sola tarea a la vez, y nunca junto con el ciclo del planificador: se usa
el mismo candado. Dos corridas a la vez gastarían Apify por duplicado y
escribirían la misma base al mismo tiempo.

El estado vive en memoria: si el servidor se reinicia a mitad, se pierde el
aviso, no el trabajo hecho (lo que alcanzó a guardarse está en la base).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

registro = logging.getLogger("pulserival.tareas")

NOMBRES = {
    "buscar": "Buscar competidores",
    "generar": "Recolectar y generar el reporte",
    "rehacer": "Rehacer el reporte",
}


class Tareas:
    def __init__(self, conectar: Callable, candado: threading.Lock | None = None) -> None:
        self._conectar = conectar
        self._candado = candado or threading.Lock()
        self._estado: dict[int, dict[str, Any]] = {}

    def estado(self, cliente_id: int) -> dict[str, Any] | None:
        return self._estado.get(cliente_id)

    def ocupado(self) -> bool:
        if not self._candado.acquire(blocking=False):
            return True
        self._candado.release()
        return False

    def lanzar(self, cliente_id: int, tipo: str,
               trabajo: Callable[[Any], dict[str, Any]]) -> str | None:
        """Arranca la tarea. Devuelve un mensaje si no se pudo, o None."""
        if not self._candado.acquire(blocking=False):
            return ("Hay otra corrida en curso (del panel o del ciclo programado). "
                    "Espere a que termine y vuelva a intentarlo.")
        self._estado[cliente_id] = {"tipo": tipo, "nombre": NOMBRES.get(tipo, tipo),
                                    "estado": "corriendo", "inicio": time.time()}

        def correr() -> None:
            con = self._conectar()
            try:
                resultado = trabajo(con)
                con.commit()
                self._estado[cliente_id].update(estado="terminada", resultado=resultado)
            except Exception as e:  # el aviso tiene que llegar al panel
                registro.exception("La tarea %s del cliente %s falló", tipo, cliente_id)
                self._estado[cliente_id].update(estado="fallo", error=str(e))
            finally:
                self._estado[cliente_id]["fin"] = time.time()
                con.close()
                self._candado.release()

        threading.Thread(target=correr, name=f"tarea-{tipo}-{cliente_id}",
                         daemon=True).start()
        return None
