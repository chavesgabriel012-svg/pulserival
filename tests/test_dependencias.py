"""Que toda librería externa que el código importa esté en requirements.txt.

Este test existe por un error real: la primera versión de requirements.txt se
olvidó de PyYAML. En la máquina donde se escribió el código ya estaba
instalado, así que todo funcionaba; en una máquina limpia (el servidor de
GitHub) la corrida murió con `ModuleNotFoundError: No module named 'yaml'`.

Es el clásico "en mi máquina funciona". Este test lo vuelve imposible.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
PAQUETE = RAIZ / "pulserival"

# Nombre con el que se importa -> nombre con el que se instala.
EQUIVALENCIAS = {"yaml": "pyyaml", "jinja2": "jinja2", "requests": "requests"}


def modulos_importados() -> set[str]:
    """Los módulos de primer nivel que importa el paquete."""
    encontrados: set[str] = set()
    for archivo in PAQUETE.rglob("*.py"):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    encontrados.add(alias.name.split(".")[0])
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.level == 0 and nodo.module:      # nivel 0 = no es import relativo
                    encontrados.add(nodo.module.split(".")[0])
    return encontrados


def declarados() -> set[str]:
    texto = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
    nombres = set()
    for linea in texto.splitlines():
        linea = linea.split("#")[0].strip()
        if not linea:
            continue
        for separador in (">=", "==", "~=", "<=", ">", "<", "["):
            linea = linea.split(separador)[0]
        nombres.add(linea.strip().lower())
    return nombres


class TestDependencias(unittest.TestCase):
    def test_todo_import_externo_esta_declarado(self):
        externos = {
            m for m in modulos_importados()
            if m not in sys.stdlib_module_names and m not in ("pulserival", "tests")
        }
        faltantes = {
            m for m in externos
            if EQUIVALENCIAS.get(m, m).lower() not in declarados()
        }
        self.assertEqual(
            faltantes, set(),
            f"Estos módulos se importan pero no están en requirements.txt: {sorted(faltantes)}. "
            "En una máquina limpia (el servidor del cron) la corrida va a fallar.",
        )

    def test_solo_las_tres_librerias_esperadas(self):
        """Si algún día se agrega una dependencia, este test falla a propósito:
        te obliga a decidir conscientemente si vale la pena mantenerla."""
        self.assertEqual(declarados(), {"pyyaml", "jinja2", "requests"})
