"""Dónde queda un alta cuando el servidor web no tiene base de datos.

Lo que importa probar acá es que el camino sin base valida EXACTAMENTE lo mismo
que el camino con base, y que lo que deposita queda inactivo. Un alta que llega
de un formulario público y termina activa es dinero de scraper gastado por un
desconocido; un alta que se pierde en silencio es un cliente perdido.
"""
from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from unittest import mock

import yaml

from pulserival import altas, sincronizar
from pulserival.web import deposito
from tests.base import CasoBase


class RespuestaFalsa:
    def __init__(self, status_code=201, text="{}"):
        self.status_code = status_code
        self.text = text


def _cuerpo_enviado(put: mock.Mock, llamada: int = 0) -> dict:
    """El YAML que se le mandó a GitHub, ya decodificado."""
    enviado = json.loads(put.call_args_list[llamada].kwargs["data"])
    return yaml.safe_load(base64.b64decode(enviado["content"]).decode("utf-8"))


class CasoGitHub(unittest.TestCase):
    def setUp(self):
        self.deposito = deposito.DepositoGitHub(
            repo="alguien/pulserival", token="ghp_falso", rama="main")
        self.alta = {
            "empresa": "Ferretería El Tornillo",
            "email": "ana@tornillo.test",
            "contacto": "Ana",
            "plan": "mensual",
            "industria": "ferreterías",
            "notas": "Tres sucursales en Alajuela.",
            "competidores": [
                {"nombre": "EPA", "meta_pagina_url": "facebook.com/EPACostaRica",
                 "google_dominio": "epa.cr", "prioridad": 1},
            ],
        }


class TestConfiguracion(CasoGitHub):
    def test_avisa_si_falta_el_repositorio_o_el_token(self):
        vacio = deposito.DepositoGitHub(repo="", token="")
        faltan = " ".join(vacio.pendientes())
        self.assertIn("PULSERIVAL_REPO", faltan)
        self.assertIn("PULSERIVAL_GITHUB_TOKEN", faltan)

    def test_sin_configurar_no_guarda_y_lo_dice(self):
        vacio = deposito.DepositoGitHub(repo="", token="")
        with self.assertRaises(deposito.DepositoError):
            vacio.guardar(self.alta)

    def test_configurado_no_tiene_pendientes(self):
        self.assertEqual(self.deposito.pendientes(), [])


class TestLoQueDeposita(CasoGitHub):
    def test_guarda_un_yaml_por_alta_con_la_fecha_en_el_nombre(self):
        with mock.patch("requests.put", return_value=RespuestaFalsa()) as put:
            resultado = self.deposito.guardar(self.alta)
        url = put.call_args.args[0]
        self.assertIn("/repos/alguien/pulserival/contents/config/altas/", url)
        self.assertTrue(url.endswith("-ferreteria-el-tornillo.yaml"), url)
        self.assertEqual(resultado["referencia"].split("/")[1], "altas")
        # Sin base no hay id de cliente, y quien llama tiene que poder notarlo
        # para no mandar a un checkout que no existe.
        self.assertIsNone(resultado["cliente_id"])

    def test_lo_que_deposita_NO_queda_activo(self):
        # Las dos banderas, no una: `activo` lo saltea el pipeline entero y
        # `estado_suscripcion` lo saltea la lógica de planes. Con una sola, un
        # cambio en el otro lado dejaría el alta gastando scraper sin pagar.
        with mock.patch("requests.put", return_value=RespuestaFalsa()) as put:
            self.deposito.guardar(self.alta)
        entrada = _cuerpo_enviado(put)["clientes"][0]
        self.assertIs(entrada["activo"], False)
        self.assertEqual(entrada["estado_suscripcion"], "pendiente_pago")

    def test_el_yaml_depositado_lo_entiende_sincronizar(self):
        # Es el punto de todo el mecanismo: el archivo lo lee `aplicar-config`.
        # Si el formato no coincide, el alta se deposita bien y nunca llega a
        # la base.
        with mock.patch("requests.put", return_value=RespuestaFalsa()) as put:
            self.deposito.guardar(self.alta)
        entradas = sincronizar.validar(_cuerpo_enviado(put))
        self.assertEqual(len(entradas), 1)
        self.assertEqual(entradas[0]["empresa"], "Ferretería El Tornillo")

    def test_conserva_el_contexto_del_negocio(self):
        with mock.patch("requests.put", return_value=RespuestaFalsa()) as put:
            self.deposito.guardar(self.alta)
        entrada = _cuerpo_enviado(put)["clientes"][0]
        self.assertEqual(entrada["industria"], "ferreterías")
        self.assertIn("Alajuela", entrada["notas"])
        self.assertEqual(entrada["contacto_email"], "ana@tornillo.test")

    def test_el_encabezado_explica_como_activarla(self):
        # Este archivo lo va a leer una persona decidiendo si gasta plata.
        with mock.patch("requests.put", return_value=RespuestaFalsa()) as put:
            self.deposito.guardar(self.alta)
        enviado = json.loads(put.call_args.kwargs["data"])
        texto = base64.b64decode(enviado["content"]).decode("utf-8")
        self.assertIn("NO está activa", texto)
        self.assertIn("aplicar-config", texto)
        self.assertIn("no están verificados", texto)

    def test_valida_lo_mismo_que_el_camino_con_base(self):
        casos = [
            ({"email": "no-es-un-correo"}, "correo"),
            ({"empresa": ""}, "empresa"),
            ({"competidores": []}, "competidor"),
            ({"plan": "inventado"}, "plan"),
            ({"competidores": [{"nombre": "Sin datos"}]}, "Facebook"),
        ]
        for cambio, esperado in casos:
            with self.subTest(cambio=cambio):
                with mock.patch("requests.put") as put:
                    with self.assertRaises(altas.AltaInvalida) as e:
                        self.deposito.guardar({**self.alta, **cambio})
                self.assertIn(esperado, str(e.exception))
                # Y sobre todo: no llamó a GitHub. Depositar un alta inválida
                # dejaría basura en el repositorio que hay que borrar a mano.
                put.assert_not_called()

    def test_la_misma_empresa_dos_veces_el_mismo_dia_no_se_sobreescribe(self):
        respuestas = [RespuestaFalsa(422, "already exists"), RespuestaFalsa(201)]
        with mock.patch("requests.put", side_effect=respuestas) as put:
            self.deposito.guardar(self.alta)
        self.assertEqual(put.call_count, 2)
        self.assertNotEqual(put.call_args_list[0].args[0], put.call_args_list[1].args[0])

    def test_un_error_de_github_no_se_traga(self):
        with mock.patch("requests.put", return_value=RespuestaFalsa(401, "bad token")):
            with self.assertRaises(deposito.DepositoError) as e:
                self.deposito.guardar(self.alta)
        self.assertIn("401", str(e.exception))


class TestEleccionDeDeposito(unittest.TestCase):
    def test_por_defecto_sqlite(self):
        with mock.patch.object(deposito.config, "env", return_value=None):
            self.assertEqual(deposito.obtener(None).nombre, "sqlite")

    def test_en_vercel_por_defecto_github(self):
        # Sin esto, un deploy en Vercel sin variables arrancaría con sqlite y
        # perdería cada alta en silencio: el disco es efímero.
        def env(clave, defecto=None):
            return "1" if clave == "VERCEL" else None

        with mock.patch.object(deposito.config, "env", side_effect=env):
            self.assertEqual(deposito.obtener(None).nombre, "github")

    def test_sqlite_en_vercel_avisa_por_salud(self):
        def env(clave, defecto=None):
            return "1" if clave == "VERCEL" else None

        with mock.patch.object(deposito.config, "env", side_effect=env):
            faltan = deposito.DepositoSqlite(None).pendientes()
        self.assertTrue(faltan)
        self.assertIn("efímero", faltan[0])

    def test_un_nombre_que_no_existe_falla_claro(self):
        with self.assertRaises(deposito.DepositoError) as e:
            deposito.obtener(None, nombre="postgres")
        self.assertIn("sqlite", str(e.exception))


class TestBandejaDeAltas(CasoBase):
    """`aplicar-config` tiene que leer config/altas/ además de clientes.yaml."""

    def _escribir(self, nombre: str, texto: str) -> Path:
        self.altas = Path(self.tmp.name) / "altas"
        self.altas.mkdir(exist_ok=True)
        ruta = self.altas / nombre
        ruta.write_text(texto, encoding="utf-8")
        return ruta

    def _alta_valida(self, clave="ferreteria", empresa="Ferretería El Tornillo") -> str:
        return yaml.safe_dump({"clientes": [{
            "clave": clave, "empresa": empresa,
            "contacto_email": "ana@tornillo.test",
            "estado_suscripcion": "pendiente_pago", "activo": False,
            "competidores": [{"nombre": "EPA", "google_dominio": "epa.cr"}],
        }]}, allow_unicode=True)

    def test_lee_las_altas_del_directorio(self):
        self._escribir("2026-01-01-ferreteria.yaml", self._alta_valida())
        clientes, avisos = sincronizar.leer_altas(self.altas)
        self.assertEqual([c["clave"] for c in clientes], ["ferreteria"])
        self.assertEqual(avisos, [])

    def test_un_archivo_roto_NO_tira_la_corrida(self):
        # Es una bandeja escrita por un proceso web con datos de un
        # desconocido. Si un archivo malo hiciera fallar `aplicar-config`, los
        # clientes que sí pagaron se quedarían sin reporte por culpa de él.
        self._escribir("2026-01-01-ferreteria.yaml", self._alta_valida())
        self._escribir("2026-01-02-roto.yaml", "clientes:\n  - empresa: sin clave\n")
        clientes, avisos = sincronizar.leer_altas(self.altas)
        self.assertEqual([c["clave"] for c in clientes], ["ferreteria"])
        self.assertEqual(len(avisos), 1)
        self.assertIn("roto.yaml", avisos[0])

    def test_sin_directorio_no_pasa_nada(self):
        self.assertEqual(sincronizar.leer_altas(Path(self.tmp.name) / "no-existe"), ([], []))

    def test_clientes_yaml_gana_sobre_un_alta_con_la_misma_clave(self):
        # Una bandeja de entrada pública no puede cambiarle los datos a un
        # cliente que ya está cargado y pagando.
        curado = Path(self.tmp.name) / "clientes.yaml"
        curado.write_text(yaml.safe_dump({"clientes": [{
            "clave": "ferreteria", "empresa": "El Tornillo (revisado)",
            "contacto_email": "real@tornillo.test",
            "competidores": [{"nombre": "EPA", "google_dominio": "epa.cr"}],
        }]}, allow_unicode=True), encoding="utf-8")
        self._escribir("2026-01-01-ferreteria.yaml",
                       self._alta_valida(empresa="El Tornillo (del formulario)"))
        clientes, avisos = sincronizar.leer_todo(curado, self.altas)
        self.assertEqual([c["empresa"] for c in clientes], ["El Tornillo (revisado)"])
        self.assertIn("ya está en clientes.yaml", avisos[0])

    def test_aplicar_carga_el_alta_a_la_base_pero_inactiva(self):
        self._escribir("2026-01-01-ferreteria.yaml", self._alta_valida())
        curado = Path(self.tmp.name) / "clientes.yaml"
        curado.write_text("clientes: []\n", encoding="utf-8")
        with mock.patch.object(sincronizar, "leer", return_value=[]), \
             mock.patch.object(sincronizar.config, "DIR_CONFIG", Path(self.tmp.name)):
            resumen = sincronizar.aplicar(self.con)
        self.con.commit()
        from pulserival import db, planes

        fila = db.fila(self.con, "SELECT * FROM clientes WHERE clave = 'ferreteria'")
        self.assertIsNotNone(fila)
        self.assertEqual(fila["activo"], 0)
        self.assertFalse(planes.reporta(fila))
        self.assertIn("ferreteria", resumen["creados"])
