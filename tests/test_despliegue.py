"""Los archivos de despliegue, que no se prueban solos hasta que fallan en vivo.

Un error acá no rompe ningún test y no se nota hasta que el deploy queda
colgado o el servicio responde "Application failed to respond". Estas
comprobaciones son baratas y cubren exactamente los errores que ya pasaron o
que están a un "lo limpio un poco" de distancia.
"""
from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PUERTO_POR_DEFECTO = 8080


class TestDockerfile(unittest.TestCase):
    def setUp(self):
        self.texto = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
        self.cmd = self.texto[self.texto.index("CMD "):]

    def test_el_puerto_sale_de_la_variable_PORT(self):
        # Railway inyecta PORT y exige que la app escuche ahí. Con el puerto
        # fijo el contenedor arranca, parece sano, y Railway responde
        # "Application failed to respond" porque nadie escucha donde mira.
        self.assertIn("${PORT:-", self.cmd)
        self.assertIn(f"${{PORT:-{PUERTO_POR_DEFECTO}}}", self.cmd)

    def test_el_CMD_es_forma_shell(self):
        # La forma de lista NO expande variables: gunicorn recibiría el texto
        # literal "$PORT" y moriría con "'$PORT' is not a valid port number".
        self.assertTrue(self.cmd.startswith('CMD ["sh", "-c"'), self.cmd[:60])

    def test_gunicorn_corre_con_exec(self):
        # Sin exec, sh queda de PID 1 y no le pasa las señales a gunicorn: el
        # apagado se vuelve un kill a los diez segundos en cada deploy.
        self.assertIn("exec gunicorn", self.cmd)

    def test_un_solo_worker(self):
        # SQLite aguanta un escritor, y el planificador corre dentro del
        # proceso web: con dos workers habría dos planificadores compitiendo
        # por la misma base.
        self.assertIn("--workers 1", self.cmd)

    def test_no_hay_un_Procfile_que_pise_el_CMD(self):
        # Railway lee el `web:` del Procfile y lo usa como comando de arranque,
        # por encima del CMD del Dockerfile, y lo ejecuta SIN shell. El Procfile
        # tenía `--bind 0.0.0.0:$PORT` literal, así que el contenedor moría en
        # bucle con "'$PORT' is not a valid port number" mientras el Dockerfile,
        # que sí expandía la variable, no se usaba nunca.
        #
        # Un solo lugar donde vive el comando de arranque. Si alguien necesita
        # un Procfile para otro hosting, que use la misma forma shell del
        # Dockerfile, no el $PORT pelado.
        procfile = RAIZ / "Procfile"
        if procfile.exists():
            texto = procfile.read_text(encoding="utf-8")
            self.assertNotIn("$PORT", texto.replace("${PORT", ""),
                             "El Procfile tiene $PORT sin expandir")
            self.assertIn("sh -c", texto,
                          "El Procfile tiene que usar forma shell, como el Dockerfile")

    def test_la_base_no_queda_dentro_de_la_imagen(self):
        ignorados = (RAIZ / ".dockerignore").read_text(encoding="utf-8")
        for carpeta in ("datos/", "borradores/", ".env"):
            self.assertIn(carpeta, ignorados)


class TestFly(unittest.TestCase):
    def setUp(self):
        self.cfg = tomllib.loads((RAIZ / "fly.toml").read_text(encoding="utf-8"))

    def test_el_puerto_interno_coincide_con_el_del_Dockerfile(self):
        # Fly no inyecta PORT, así que la app cae al valor por defecto del
        # Dockerfile. Si los dos números se separan, Fly manda el tráfico a un
        # puerto donde no hay nadie.
        self.assertEqual(self.cfg["http_service"]["internal_port"], PUERTO_POR_DEFECTO)

    def test_la_maquina_no_se_apaga_sola(self):
        # Una máquina dormida despierta con una petición, no con un horario.
        # Con auto_stop encendido el planificador no correría nunca.
        self.assertFalse(self.cfg["http_service"]["auto_stop_machines"])
        self.assertGreaterEqual(self.cfg["http_service"]["min_machines_running"], 1)

    def test_hay_disco_montado(self):
        self.assertTrue(self.cfg["mounts"]["destination"])
        # Y todo lo que tiene que sobrevivir a un deploy apunta ahí.
        for variable in ("PULSERIVAL_DB", "PULSERIVAL_BORRADORES", "PULSERIVAL_SALIDA"):
            self.assertTrue(
                self.cfg["env"][variable].startswith(self.cfg["mounts"]["destination"]),
                f"{variable} no apunta al disco")

    def test_no_hay_secretos_en_el_archivo(self):
        # fly.toml va al repositorio. Los secretos van por `fly secrets set`.
        prohibidas = ("APIFY_TOKEN", "GEMINI_API_KEY", "GROQ_API_KEY",
                      "RESEND_API_KEY", "PULSERIVAL_PANEL_CLAVE", "PULSERIVAL_SECRET")
        for clave in prohibidas:
            self.assertNotIn(clave, self.cfg["env"], f"{clave} no puede estar acá")


class TestRailway(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((RAIZ / "railway.json").read_text(encoding="utf-8"))

    def test_usa_el_Dockerfile_y_no_la_autodeteccion(self):
        # Sin esto Railway autodetecta Python y arma la imagen por su cuenta,
        # ignorando el Dockerfile y el --workers 1 que lleva adentro.
        self.assertEqual(self.cfg["build"]["builder"], "DOCKERFILE")

    def test_una_sola_replica(self):
        # Misma razón que --workers 1: dos réplicas serían dos planificadores
        # y dos escritores sobre la misma base.
        self.assertEqual(self.cfg["deploy"]["numReplicas"], 1)

    def test_el_chequeo_de_salud_apunta_a_salud(self):
        self.assertEqual(self.cfg["deploy"]["healthcheckPath"], "/salud")


if __name__ == "__main__":
    unittest.main()
