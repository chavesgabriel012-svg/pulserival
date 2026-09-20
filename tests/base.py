"""Base para los tests: una base de datos temporal por test, sin red y sin claves."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("APIFY_TOKEN", None)
os.environ.pop("RESEND_API_KEY", None)
os.environ.pop("SMTP_HOST", None)

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
