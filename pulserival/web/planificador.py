"""El cron, adentro del mismo proceso que el panel.

Por qué acá y no como tarea programada del hosting: tanto en Fly.io como en
Railway un disco se monta en UN solo contenedor. El panel escribe en la base
(guarda su versión, marca enviado) y el ciclo también. Si el ciclo corriera en
un contenedor aparte, serían dos bases distintas divergiendo en silencio, que
es exactamente el problema que hizo mover todo a un servidor.

Cómo decide, y por qué no es un cron:

Un cron dispara a una hora fija y lo que se perdió, se perdió: si la máquina
estaba reiniciándose el lunes a las 5, esa semana no hay reporte y nadie se
entera hasta que el cliente pregunta. Acá la regla es otra: cada tic mira
**cuándo fue la última recolección** y corre si ya pasó la cadencia. Una
corrida atrasada se recupera en el tic siguiente en vez de perderse.

Lo que el tic NO hace: llamar al scraper para averiguar si hay trabajo.
`ciclo_completo()` recolecta primero y recién después mira a quién le toca, así
que llamarlo cada hora pagaría Apify veinticuatro veces por día. El tic lee una
fila de la base y en el 99% de los casos no hace nada más.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import config, db

registro = logging.getLogger("pulserival.planificador")

# Cada cuánto despierta el hilo. No es la cadencia del ciclo: es cada cuánto se
# pregunta si le toca. Una hora es suficientemente fino para un reporte semanal
# y suficientemente grueso para no hacer nada todo el día.
INTERVALO_TIC_SEG = 3600
# Si hay una corrida 'en_curso' más nueva que esto, otro proceso la está
# haciendo. Protege del caso de dos workers, donde el candado de memoria no
# sirve porque cada proceso tiene el suyo.
MINUTOS_CORRIDA_VIVA = 90


def habilitado() -> bool:
    return (config.env("PULSERIVAL_PLANIFICADOR") or "0") == "1"


def cadencia_dias() -> float:
    return float(config.env("PULSERIVAL_CICLO_CADA_DIAS") or 7)


def hora_utc() -> int:
    """A qué hora UTC se prefiere correr. 11 UTC = 5:00 a.m. en Costa Rica."""
    try:
        return max(0, min(23, int(config.env("PULSERIVAL_CICLO_HORA_UTC") or 11)))
    except ValueError:
        return 11


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def ultima_corrida(con: sqlite3.Connection) -> dict[str, Any] | None:
    fila = db.fila(con, "SELECT id, iniciada_en, terminada_en, estado FROM "
                        "corridas_recoleccion ORDER BY iniciada_en DESC LIMIT 1")
    return dict(fila) if fila else None


def _a_fecha(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        # SQLite guarda 'YYYY-MM-DD HH:MM:SS' sin zona; es UTC por datetime('now').
        return datetime.fromisoformat(str(valor).replace("Z", "")).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def decidir(con: sqlite3.Connection, ahora: datetime | None = None) -> dict[str, Any]:
    """¿Corre el ciclo ahora? Solo lee la base: no gasta nada.

    Tres respuestas posibles y cada una dice por qué, porque esto es lo que se
    va a mirar cuando alguien pregunte por qué no llegó el reporte del lunes.
    """
    ahora = ahora or _ahora()
    ultima = ultima_corrida(con)

    if ultima and ultima["estado"] == "en_curso":
        iniciada = _a_fecha(ultima["iniciada_en"])
        if iniciada and (ahora - iniciada) < timedelta(minutes=MINUTOS_CORRIDA_VIVA):
            return {"correr": False, "motivo": "ya hay una corrida en curso"}

    # Cuánto hace que venció la cadencia. None = no se sabe (base nueva).
    atraso: timedelta | None = None
    iniciada = _a_fecha((ultima or {}).get("iniciada_en"))
    if not ultima:
        # Base sin historial: recién instalado, o el disco todavía no tiene la
        # base de la rama `datos`. La PRIMERA corrida no se dispara sola.
        #
        # Es la única forma de que un deploy no pueda gastar plata por su
        # cuenta, y además es el orden correcto para instalar: desplegar,
        # cargar los clientes, correr una vez a mano para comprobar que el
        # scraper y las claves andan, y recién ahí dejarlo en automático.
        # Con PULSERIVAL_CICLO_AL_ARRANCAR=1 se pide lo contrario a propósito.
        if (config.env("PULSERIVAL_CICLO_AL_ARRANCAR") or "0") != "1":
            return {"correr": False,
                    "motivo": "no hay ninguna corrida todavía: la primera se "
                              "dispara a mano (cli ciclo), no sola"}
        motivo = "no hay ninguna corrida todavía"
    elif not iniciada:
        motivo = "la última corrida no tiene fecha legible"
    else:
        transcurrido = ahora - iniciada
        faltan = timedelta(days=cadencia_dias()) - transcurrido
        if faltan > timedelta(0):
            return {"correr": False,
                    "proxima_en_horas": round(faltan.total_seconds() / 3600, 1),
                    "motivo": f"la última corrida fue hace {transcurrido.days} días"}
        atraso = -faltan
        motivo = f"pasaron {transcurrido.days} días desde la última corrida"

    # Se espera a la hora preferida. Esto también cubre el arranque en limpio:
    # sin este freno, desplegar a las 3 de la tarde con la base recién puesta
    # disparaba una corrida real —con su gasto de Apify— en el momento, sin
    # que nadie la hubiera pedido. Un deploy no puede gastar plata solo.
    #
    # Salvo que el atraso ya pase de un día: ahí esperar a la hora exacta de
    # mañana sería perder otro día por prolijidad.
    if ahora.hour < hora_utc() and (atraso is None or atraso < timedelta(days=1)):
        return {"correr": False,
                "motivo": f"{motivo}; esperando las {hora_utc():02d}:00 UTC"}

    return {"correr": True, "motivo": motivo}


class Planificador:
    """Un hilo que despierta cada hora y corre el ciclo cuando toca."""

    def __init__(self, conectar, intervalo: int = INTERVALO_TIC_SEG) -> None:
        self._conectar = conectar
        self._intervalo = intervalo
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        # Que dos tics no se solapen dentro de este proceso. Entre procesos
        # lo cubre la comprobación de corrida 'en_curso' de `decidir()`.
        self._corriendo = threading.Lock()
        self.ultimo_resultado: dict[str, Any] | None = None

    def arrancar(self) -> None:
        if self._hilo and self._hilo.is_alive():
            return
        # daemon: si el proceso web se cae, el hilo no lo mantiene vivo.
        self._hilo = threading.Thread(target=self._bucle, name="planificador",
                                      daemon=True)
        self._hilo.start()
        registro.info(
            "Planificador encendido: tic cada %s min, cadencia %s días, "
            "hora preferida %02d:00 UTC",
            self._intervalo // 60, cadencia_dias(), hora_utc())

    def parar(self) -> None:
        self._parar.set()

    def _bucle(self) -> None:
        while not self._parar.is_set():
            try:
                self.tic()
            except Exception:
                # Una excepción acá mataría el hilo y el cron dejaría de
                # existir sin que nadie se entere. Se registra y se sigue.
                registro.exception("El tic del planificador falló")
            self._parar.wait(self._intervalo)

    def tic(self) -> dict[str, Any]:
        """Un tic: decide y, si toca, corre el ciclo. Devuelve qué hizo."""
        con = self._conectar()
        try:
            decision = decidir(con)
        finally:
            con.close()
        if not decision["correr"]:
            registro.debug("Planificador: %s", decision["motivo"])
            return {**decision, "corrio": False}

        if not self._corriendo.acquire(blocking=False):
            return {"correr": False, "corrio": False,
                    "motivo": "el tic anterior todavía está corriendo"}
        try:
            registro.info("Planificador: arrancando el ciclo (%s)", decision["motivo"])
            resultado = self._correr_ciclo()
            self.ultimo_resultado = resultado
            return {**decision, "corrio": True, "resultado": resultado}
        finally:
            self._corriendo.release()

    def _correr_ciclo(self) -> dict[str, Any]:
        from .. import pipeline

        modo = config.env("PULSERIVAL_CICLO_MODO") or "auto"
        try:
            limite = int(config.env("PULSERIVAL_CICLO_LIMITE") or 40)
        except ValueError:
            limite = 40
        con = self._conectar()
        try:
            resultado = pipeline.ciclo_completo(con, modo=modo, limite=limite)
            con.commit()
        except Exception as e:
            registro.exception("El ciclo falló")
            return {"error": str(e)}
        finally:
            con.close()
        hechos = [r for r in resultado.get("reportes", []) if r.get("reporte_id")]
        registro.info("Planificador: ciclo terminado, %s reporte(s) generado(s)",
                      len(hechos))
        return {"reportes": len(hechos),
                "clientes": len(resultado.get("reportes", []))}


def estado(con: sqlite3.Connection) -> dict[str, Any]:
    """Para /salud y para el panel: cuándo corrió y cuándo vuelve a correr."""
    if not habilitado():
        return {"planificador": "apagado (PULSERIVAL_PLANIFICADOR distinto de 1)"}
    ultima = ultima_corrida(con)
    decision = decidir(con)
    return {
        "planificador": "encendido",
        "cadencia_dias": cadencia_dias(),
        "hora_utc": hora_utc(),
        "ultima_corrida": (ultima or {}).get("iniciada_en"),
        "estado_ultima": (ultima or {}).get("estado"),
        "siguiente": decision["motivo"],
    }
