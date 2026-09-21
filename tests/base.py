"""Base para los tests: una base de datos temporal por test, sin red y sin claves."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Los tests nunca deben llamar a un servicio real ni gastar crédito.
#
# Ojo con el detalle: borrar estas variables NO alcanza. config.cargar_env()
# lee el .env del proyecto y usa setdefault, así que una clave borrada vuelve
# a aparecer y los tests terminan haciendo llamadas de verdad (esto pasó: un
# test unitario recibió un 401 de la API de correo). Ponerlas en vacío sí
# funciona: setdefault no pisa una variable que ya existe, y config.env()
# devuelve None cuando el valor está vacío.
CLAVES_BLOQUEADAS = (
    "GROQ_API_KEY", "GEMINI_API_KEY", "APIFY_TOKEN",
    "RESEND_API_KEY", "SMTP_HOST", "META_AD_LIBRARY_TOKEN",
)
for _clave in CLAVES_BLOQUEADAS:
    os.environ[_clave] = ""

from pulserival import config, db  # noqa: E402


class CasoBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "prueba.db"
        os.environ["PULSERIVAL_DB"] = str(self.ruta)
        db.inicializar(self.ruta)
        self.con = db.conectar(self.ruta)
        self.addCleanup(self.con.close)
        # Los tests no escriben en las carpetas reales del proyecto.
        originales = (config.DIR_BORRADORES, config.DIR_SALIDA)
        config.DIR_BORRADORES = Path(self.tmp.name) / "borradores"
        config.DIR_SALIDA = Path(self.tmp.name) / "salida"

        def restaurar() -> None:
            config.DIR_BORRADORES, config.DIR_SALIDA = originales

        self.addCleanup(restaurar)
        self.addCleanup(self.tmp.cleanup)

    def cliente(self, **extra) -> int:
        datos = {"nombre_empresa": "Gimnasio Fuerza Tica",
                 "contacto_email": "ana@ejemplo.test",
                 "periodicidad": "semanal", "industria": "gimnasios"}
        datos.update(extra)
        cid = db.insertar(self.con, "clientes", datos)
        self.con.commit()
        return cid

    def competidor(self, cliente_id: int, nombre: str = "Vital Gym CR", **extra) -> int:
        datos = {"cliente_id": cliente_id, "nombre": nombre,
                 "meta_consulta": nombre, "google_dominio": "ejemplo.test"}
        datos.update(extra)
        cid = db.insertar(self.con, "competidores_seguidos", datos)
        self.con.commit()
        return cid
