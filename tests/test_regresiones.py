"""Un test por cada bug que la revisión de código encontró.

Cada uno falla si el bug vuelve. El comentario dice qué pasaba antes, porque
dentro de seis meses eso es lo único que explica por qué el test existe.
"""
from __future__ import annotations

import os
import unittest

from pulserival import db, pipeline, priorizar, util
from pulserival.fuentes.base import AnuncioCrudo
from pulserival.reporte import generar as generar_mod
from pulserival.reporte import render
from pulserival.revision import flujo
from tests.base import CLAVES_BLOQUEADAS, CasoBase


def anuncio(**kw) -> AnuncioCrudo:
    base = dict(plataforma="meta", fuente="test", id_externo="X1", titulo="Matrícula gratis",
                texto="Plan mensual ¢19.900", link_destino="https://ejemplo.test/promo")
    base.update(kw)
    return AnuncioCrudo(**base)


class TestSeguridadDeLosTests(unittest.TestCase):
    """Antes: borrar las claves del entorno no servía porque cargar_env() las
    volvía a poner desde el .env, y los tests hacían llamadas reales (se llegó
    a recibir un 401 de la API de correo dentro de un test unitario)."""

    def test_ningun_proveedor_real_esta_disponible(self):
        from pulserival.config import env
        from pulserival.ia.gemini_proveedor import ProveedorGemini
        from pulserival.ia.groq_proveedor import ProveedorGroq

        for clave in CLAVES_BLOQUEADAS:
            self.assertIsNone(env(clave), f"{clave} no debería estar visible en los tests")
        self.assertFalse(ProveedorGroq().disponible())
        self.assertFalse(ProveedorGemini().disponible())


class TestPeriodo(unittest.TestCase):
    """Antes: el periodo iba de hoy-7 a hoy con ambos extremos incluidos, así
    que el día del borde caía en dos reportes seguidos y el mismo anuncio se
    reportaba dos veces como nuevo."""

    def test_semanas_consecutivas_no_comparten_dias(self):
        import datetime as dt

        hoy = dt.date(2026, 9, 20)
        i1, f1 = util.periodo("semanal", hoy)
        i2, f2 = util.periodo("semanal", hoy - dt.timedelta(days=7))
        self.assertEqual((i1, f1), ("2026-09-14", "2026-09-20"))
        self.assertEqual((i2, f2), ("2026-09-07", "2026-09-13"))
        self.assertLess(f2, i1, "el fin de un periodo no puede caer dentro del siguiente")

    def test_el_periodo_semanal_cubre_siete_dias(self):
        import datetime as dt

        inicio, fin = util.periodo("semanal", dt.date(2026, 9, 20))
        dias = (dt.date.fromisoformat(fin) - dt.date.fromisoformat(inicio)).days + 1
        self.assertEqual(dias, 7)

    def test_el_periodo_mensual_cubre_treinta_dias(self):
        import datetime as dt

        inicio, fin = util.periodo("mensual", dt.date(2026, 9, 30))
        dias = (dt.date.fromisoformat(fin) - dt.date.fromisoformat(inicio)).days + 1
        self.assertEqual(dias, 30)


class TestHuella(unittest.TestCase):
    """Antes: la huella guardaba solo el dominio del link, así que cambiar la
    página de destino (de /promo-setiembre a /black-friday) con el mismo texto
    pasaba como 'sigue igual'."""

    def test_cambiar_la_ruta_del_link_es_un_cambio(self):
        a = anuncio(link_destino="https://ejemplo.test/promo-setiembre")
        b = anuncio(link_destino="https://ejemplo.test/black-friday")
        self.assertNotEqual(a.huella(), b.huella())

    def test_cambiar_los_parametros_de_campania_no_es_un_cambio(self):
        a = anuncio(link_destino="https://ejemplo.test/promo?utm_source=fb&fbclid=1")
        b = anuncio(link_destino="https://ejemplo.test/promo?utm_source=ig")
        self.assertEqual(a.huella(), b.huella())

    def test_la_barra_final_no_cuenta(self):
        self.assertEqual(anuncio(link_destino="https://ejemplo.test/promo/").huella(),
                         anuncio(link_destino="https://ejemplo.test/promo").huella())


class TestRenderEnlaces(unittest.TestCase):
    """Antes: la regla de itálicas corría después de armar los links y
    convertía los guiones bajos de la URL en <em>, partiendo el href justo de
    los links de trazabilidad a la fuente."""

    def test_guion_bajo_en_la_url_no_rompe_el_enlace(self):
        html = render._inline(
            "Ver [el anuncio](https://www.facebook.com/ads/library/?id=123_456_789)")
        self.assertIn('href="https://www.facebook.com/ads/library/?id=123_456_789"', html)
        self.assertNotIn("<em>", html)

    def test_la_italica_de_verdad_sigue_funcionando(self):
        self.assertIn("<em>así</em>", render._inline("texto _así_ escrito"))


class TestPriorizarRegresiones(CasoBase):
    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.comp_id = self.competidor(self.cli)
        self.comp = db.fila(self.con, "SELECT * FROM competidores_seguidos WHERE id = ?",
                            (self.comp_id,))

    def conciliar(self, anuncios):
        r = priorizar.conciliar(self.con, self.comp, "meta", anuncios)
        self.con.commit()
        return r

    def test_corrida_vacia_no_apaga_todo(self):
        """Antes: si el scraper respondía 200 con lista vacía (falla silenciosa
        por cambio de HTML o bloqueo), TODOS los anuncios del competidor
        quedaban 'pausados' y el reporte le decía al cliente que su competencia
        apagó toda la pauta. Falso y alarmista."""
        self.conciliar([anuncio(), anuncio(id_externo="X2", titulo="Otro")])
        r = self.conciliar([])
        self.assertTrue(r.sospechosa)
        self.assertEqual(r.resumen()["pausados"], 0)
        activos = db.fila(self.con,
                          "SELECT COUNT(*) AS n FROM anuncios_detectados WHERE estado = 'activo'")["n"]
        self.assertEqual(activos, 2, "no se debe tocar nada en una corrida sospechosa")

    def test_primera_corrida_vacia_no_es_sospechosa(self):
        r = self.conciliar([])
        self.assertFalse(r.sospechosa, "sin historial, vacío es simplemente vacío")

    def test_volver_a_una_version_anterior_es_cambio_no_pausa(self):
        """Antes: A -> B -> A daba 'continua: 1' y además un 'pausado: 1'
        fantasma, o sea el reporte anunciaba un anuncio caído que nunca se cayó."""
        self.conciliar([anuncio(texto="version A")])
        self.conciliar([anuncio(texto="version B")])
        r = self.conciliar([anuncio(texto="version A")])
        self.assertEqual(r.resumen()["cambiados"], 1)
        self.assertEqual(r.resumen()["pausados"], 0)
        self.assertEqual(r.resumen()["continuan"], 0)

    def test_la_corrida_sospechosa_llega_al_pipeline(self):
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        pipeline.recolectar(self.con, modo="demo")
        vacia = priorizar.conciliar(self.con, self.comp, "meta", [])
        self.assertTrue(vacia.sospechosa)


class TestClienteDesactivado(CasoBase):
    """Antes: `--cliente N` ignoraba el campo activo, así que un cliente dado
    de baja seguía consumiendo scraper y generando reportes."""

    def test_no_se_recolecta_un_cliente_desactivado(self):
        cli = self.cliente()
        self.competidor(cli)
        self.con.execute("UPDATE clientes SET activo = 0 WHERE id = ?", (cli,))
        self.con.commit()
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        corrida = pipeline.recolectar(self.con, cliente_id=cli, modo="demo")
        self.assertEqual(corrida.totales()["nuevos"], 0)
        self.assertEqual(db.clientes_activos(self.con, cli), [])


class TestCadencia(CasoBase):
    """Antes: el cron semanal generaba un borrador nuevo para los clientes
    mensuales en cada corrida: cuatro reportes por mes, cada uno pagando IA."""

    def test_cliente_mensual_no_recibe_reporte_cada_semana(self):
        cli = self.cliente(periodicidad="mensual")
        self.competidor(cli)
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        primero = pipeline.ciclo_completo(self.con, modo="demo")
        self.assertIn("reporte_id", primero["reportes"][0])
        segundo = pipeline.ciclo_completo(self.con, modo="demo")
        self.assertIn("omitido", segundo["reportes"][0])

    def test_cliente_semanal_si_recibe_cada_semana(self):
        cli = self.cliente(periodicidad="semanal")
        self.competidor(cli)
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        pipeline.ciclo_completo(self.con, modo="demo")
        # Un reporte de la semana pasada no debe frenar el de esta semana.
        inicio, fin = util.periodo("semanal")
        self.con.execute("UPDATE reportes_generados SET periodo_fin = date(?, '-7 day'), "
                         "periodo_inicio = date(?, '-7 day') WHERE cliente_id = ?",
                         (fin, inicio, cli))
        self.con.commit()
        segundo = pipeline.ciclo_completo(self.con, modo="demo")
        self.assertIn("reporte_id", segundo["reportes"][0])


class TestSegurosDelReporte(CasoBase):
    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.competidor(self.cli)
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        pipeline.recolectar(self.con, modo="demo")
        self.cliente_row = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (self.cli,))
        self.inicio, self.fin = util.periodo("semanal")
        self.rep = generar_mod.generar(self.con, self.cliente_row, self.inicio, self.fin)["reporte_id"]
        self.con.commit()

    def test_no_se_registra_version_final_de_un_reporte_ya_enviado(self):
        """Antes: registrar_final volvía a poner el reporte en 'revisado' y
        borraba la fecha de envío del camino, con lo que se podía mandar dos
        veces el mismo reporte al cliente."""
        flujo.registrar_final(self.con, self.rep, final_md="texto final", autoetiquetar=False)
        db.actualizar(self.con, "reportes_generados", self.rep,
                      {"estado": "enviado", "enviado_en": util.ahora_iso()})
        self.con.commit()
        with self.assertRaises(RuntimeError) as ctx:
            flujo.registrar_final(self.con, self.rep, final_md="otra cosa", autoetiquetar=False)
        self.assertIn("ya se envió", str(ctx.exception))
        estado = db.fila(self.con, "SELECT estado FROM reportes_generados WHERE id = ?",
                         (self.rep,))["estado"]
        self.assertEqual(estado, "enviado")

    def test_regenerar_descarta_la_version_final_vieja(self):
        """Antes: --regenerar reescribía el borrador pero dejaba el final_md
        anterior, así que exportar y enviar seguían usando el texto viejo y el
        borrador nuevo era inalcanzable."""
        flujo.registrar_final(self.con, self.rep, final_md="TEXTO HUMANO VIEJO",
                              autoetiquetar=False)
        self.con.commit()
        generar_mod.generar(self.con, self.cliente_row, self.inicio, self.fin, regenerar=True)
        self.con.commit()
        fila = db.fila(self.con, "SELECT final_md, estado FROM reportes_generados WHERE id = ?",
                       (self.rep,))
        self.assertIsNone(fila["final_md"])
        self.assertEqual(fila["estado"], "borrador")
        cuerpo = flujo.leer_cuerpo(flujo.exportar(self.con, self.rep))
        self.assertNotIn("TEXTO HUMANO VIEJO", cuerpo)
        # La edición registrada se conserva: es dato para la Fase 2.
        self.assertEqual(db.fila(self.con, "SELECT COUNT(*) AS n FROM ediciones_registradas")["n"], 1)


class TestModoAuto(unittest.TestCase):
    """Antes: en modo 'auto' sin APIFY_TOKEN el sistema caía a los datos de
    ejemplo (un gimnasio inventado) sin decir nada. Si el token se vencía en el
    servidor, el lunes se generaba un reporte con anuncios ficticios para un
    cliente que paga."""

    def test_sin_token_el_modo_auto_falla_en_vez_de_inventar_datos(self):
        from pulserival.fuentes import FuenteError, obtener_fuente

        with self.assertRaises(FuenteError) as ctx:
            obtener_fuente("meta", "auto")
        self.assertIn("APIFY_TOKEN", str(ctx.exception))

    def test_el_modo_demo_sigue_disponible_explicitamente(self):
        from pulserival.fuentes import obtener_fuente

        self.assertTrue(obtener_fuente("meta", "demo").nombre.startswith("demo:"))

    def test_un_anuncio_de_demo_queda_marcado_en_la_base(self):
        from pulserival.fuentes import obtener_fuente

        anuncios = obtener_fuente("google", "demo").traer({"id": 1, "nombre": "X"})
        self.assertTrue(all(a.fuente.startswith("demo:") for a in anuncios))


class TestCodigoDeSalida(CasoBase):
    """Antes: si TODAS las fuentes fallaban (por ejemplo, el token vencido),
    el comando salía con código 0 y el workflow de GitHub quedaba en verde.
    Un cron verde que no recolecta nada es peor que uno rojo: no te enterás
    hasta que un cliente pregunta por su reporte."""

    def cli(self, *args) -> int:
        from pulserival.cli import main

        return main(list(args))

    def test_todas_las_fuentes_fallan_devuelve_error(self):
        cli = self.cliente()
        self.competidor(cli)
        self.assertEqual(self.cli("recolectar", "--modo", "apify"), 1,
                         "sin token, ninguna fuente funciona: tiene que salir en rojo")

    def test_corrida_exitosa_devuelve_cero(self):
        cli = self.cliente()
        self.competidor(cli)
        os.environ["PULSERIVAL_DEMO_SEMANA"] = "1"
        self.assertEqual(self.cli("recolectar", "--modo", "demo"), 0)

    def test_base_vacia_no_es_error(self):
        # Sin clientes no hay nada que recolectar, pero tampoco nada que falle.
        self.assertEqual(self.cli("recolectar", "--modo", "demo"), 0)

    def test_ciclo_tambien_devuelve_error_si_no_recolecto_nada(self):
        cli = self.cliente()
        self.competidor(cli)
        self.assertEqual(self.cli("ciclo", "--modo", "apify"), 1)


class TestHuellaDelCreativo(unittest.TestCase):
    """El CDN de Meta firma cada URL y rota de servidor en cada consulta.

    Medido en una corrida real: 68 de 77 anuncios aparecían como "cambiados"
    con el texto idéntico, solo porque la firma y el servidor eran otros. Un
    reporte que cada semana le anuncia al cliente 77 cambios que no
    ocurrieron deja de ser creíble a la segunda semana.
    """

    BASE = dict(plataforma="meta", fuente="t", id_externo="X1",
                titulo="Matrícula gratis", texto="Plan mensual")

    def anuncio(self, creativo):
        from pulserival.fuentes.base import AnuncioCrudo

        return AnuncioCrudo(creativo_url=creativo, **self.BASE)

    def test_la_firma_y_el_servidor_del_cdn_no_cuentan_como_cambio(self):
        a = self.anuncio("https://scontent-phl2-1.xx.fbcdn.net/v/t39.35426-6/"
                         "795697003_1373407654959818_n.jpg?_nc_gid=Dzl7&oh=00_AQLlSK&oe=6AB7")
        b = self.anuncio("https://scontent-lax3-1.xx.fbcdn.net/v/t39.35426-6/"
                         "795697003_1373407654959818_n.jpg?_nc_gid=NsmEr&oh=00_AQJdnd&oe=6AB7")
        self.assertEqual(a.huella(), b.huella())

    def test_cambiar_de_verdad_la_pieza_si_cuenta_como_cambio(self):
        a = self.anuncio("https://cdn.test/v/795697003_1373407654959818_n.jpg?x=1")
        b = self.anuncio("https://cdn.test/v/111111111_9999999999999999_n.jpg?x=1")
        self.assertNotEqual(a.huella(), b.huella())

    def test_se_puede_apagar_el_creativo_de_la_huella(self):
        from unittest import mock

        with mock.patch("pulserival.config.huella_incluye_creativo", return_value=False):
            a = self.anuncio("https://cdn.test/a.jpg")
            b = self.anuncio("https://cdn.test/b.jpg")
            self.assertEqual(a.huella(), b.huella(),
                             "apagado, solo el texto y el destino definen el anuncio")


class TestRecalculoDeHuellas(CasoBase):
    """Cambiar cómo se calcula la huella no puede producir un falso
    'todo cambió' en la corrida siguiente."""

    def test_recalcula_y_fusiona_los_duplicados_que_dejo_la_url_firmada(self):
        from pulserival import mantenimiento

        cli = self.cliente()
        comp = self.competidor(cli)
        base = dict(competidor_id=comp, plataforma="meta", fuente="t", id_externo="X1",
                    titulo="Promo", texto="mismo texto", estado="activo")
        # Dos filas del mismo anuncio, separadas solo por la firma del CDN.
        db.insertar(self.con, "anuncios_detectados", dict(
            base, huella="vieja1",
            creativo_url="https://scontent-a.xx.fbcdn.net/v/t39/795697003_n.jpg?oh=AAA"))
        db.insertar(self.con, "anuncios_detectados", dict(
            base, huella="vieja2",
            creativo_url="https://scontent-b.xx.fbcdn.net/v/t39/795697003_n.jpg?oh=BBB"))
        self.con.commit()

        res = mantenimiento.recalcular_huellas(self.con)
        self.assertEqual(res["duplicados_fusionados"], 1)
        quedan = db.fila(self.con, "SELECT COUNT(*) n FROM anuncios_detectados")["n"]
        self.assertEqual(quedan, 1, "las dos filas eran el mismo anuncio")

    def test_la_simulacion_no_toca_nada(self):
        from pulserival import mantenimiento

        cli = self.cliente()
        comp = self.competidor(cli)
        db.insertar(self.con, "anuncios_detectados", {
            "competidor_id": comp, "plataforma": "meta", "fuente": "t",
            "huella": "vieja", "texto": "x"})
        self.con.commit()
        mantenimiento.recalcular_huellas(self.con, aplicar=False)
        self.assertEqual(
            db.fila(self.con, "SELECT huella FROM anuncios_detectados")["huella"], "vieja")


class TestPruebaGratisNoSeRepite(CasoBase):
    """Antes: la prueba gratis se cobraba sola cada semana.

    `_toca_reportar()` decidía por aritmética de fechas: si el último reporte
    cerró antes de que arrancara el periodo actual, toca uno nuevo. Eso vale
    para quien paga, pero a la semana siguiente le abría un periodo nuevo al
    plan de prueba igual que a todos, y cada uno gasta Apify e IA. Un plan que
    se vende como "un solo reporte" habría entregado uno por semana para
    siempre.
    """

    def cliente_con_plan(self, plan, estado, **extra):
        cid = self.cliente(plan=plan, estado_suscripcion=estado, **extra)
        return db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))

    def _reporte_viejo(self, cliente_id, dias_atras=14):
        """Un reporte cuyo periodo ya cerró: por calendario, tocaría otro."""
        inicio, fin = util.periodo("semanal")
        db.insertar(self.con, "reportes_generados", {
            "cliente_id": cliente_id, "periodo_inicio": inicio, "periodo_fin": fin,
            "asunto": "x", "borrador_md": "y", "estado": "enviado",
        })
        self.con.execute(
            "UPDATE reportes_generados SET periodo_inicio = date(periodo_inicio, ?), "
            "periodo_fin = date(periodo_fin, ?) WHERE cliente_id = ?",
            (f"-{dias_atras} day", f"-{dias_atras} day", cliente_id))
        self.con.commit()

    def test_sin_recurrencia_no_abre_un_periodo_nuevo(self):
        cid = self.cliente()
        self._reporte_viejo(cid)
        inicio, _ = util.periodo("semanal")
        self.assertFalse(
            pipeline._toca_reportar(self.con, cid, "semanal", inicio, recurrente=False),
            "la prueba gratis ya gastó su único reporte")

    def test_el_que_paga_si_recibe_el_periodo_siguiente(self):
        # El mismo escenario, con recurrencia: tiene que seguir reportando.
        cid = self.cliente()
        self._reporte_viejo(cid)
        inicio, _ = util.periodo("semanal")
        self.assertTrue(
            pipeline._toca_reportar(self.con, cid, "semanal", inicio, recurrente=True))

    def test_por_defecto_sigue_siendo_recurrente(self):
        # Los clientes cargados antes de que existieran los planes no tienen
        # `plan`: no se les puede cortar el reporte por un valor que falta.
        cid = self.cliente()
        self._reporte_viejo(cid)
        inicio, _ = util.periodo("semanal")
        self.assertTrue(pipeline._toca_reportar(self.con, cid, "semanal", inicio))

    def test_una_prueba_usada_no_gasta_scraper(self):
        # No es solo que no reporte: raspar a sus competidores sería pagarle
        # Apify a una base que nadie va a leer.
        from pulserival import planes
        self.assertFalse(planes.reporta({"estado_suscripcion": "prueba_usada"}))
        self.assertFalse(planes.reporta({"estado_suscripcion": "cancelada"}))
        self.assertFalse(planes.reporta({"estado_suscripcion": "pendiente_pago"}))
        self.assertTrue(planes.reporta({"estado_suscripcion": "activa"}))
        self.assertTrue(planes.reporta({"estado_suscripcion": "prueba_pendiente"}))
        self.assertTrue(planes.reporta({}), "un cliente viejo sin estado sigue reportando")


class TestMigracionDePlanes(CasoBase):
    """Antes de los planes, `clientes` solo tenía `periodicidad` con un CHECK
    de ('semanal','mensual'). SQLite no sabe modificar un CHECK sin reconstruir
    la tabla, y reconstruirla se llevaría por delante el historial de anuncios
    y reportes. Por eso la cadencia arbitraria entra por una columna nueva.
    """

    def test_un_cliente_viejo_queda_activo_y_con_su_cadencia(self):
        # Simula una base anterior a los planes: sin las columnas nuevas.
        import sqlite3
        ruta = self.ruta.parent / "vieja.db"
        con = sqlite3.connect(ruta)
        con.executescript("""
            CREATE TABLE clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clave TEXT UNIQUE,
                nombre_empresa TEXT NOT NULL,
                contacto_nombre TEXT,
                contacto_email TEXT NOT NULL,
                contacto_whatsapp TEXT,
                periodicidad TEXT NOT NULL DEFAULT 'semanal'
                    CHECK (periodicidad IN ('semanal','mensual')),
                dia_envio TEXT DEFAULT 'martes',
                industria TEXT, notas TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO clientes (nombre_empresa, contacto_email, periodicidad)
            VALUES ('Cliente Viejo', 'viejo@ejemplo.test', 'mensual');
        """)
        con.commit()
        con.close()

        db.inicializar(ruta)

        con = sqlite3.connect(ruta)
        con.row_factory = sqlite3.Row
        fila = con.execute("SELECT * FROM clientes WHERE id = 1").fetchone()
        self.assertEqual(fila["periodicidad"], "mensual", "no se le toca la cadencia")
        self.assertEqual(fila["plan"], "mensual", "hereda el plan de su periodicidad")
        self.assertEqual(fila["estado_suscripcion"], "activa",
                         "un cliente que ya existía está activo, no en prueba")
        con.close()

    def test_el_relleno_no_pisa_un_plan_elegido_a_mano(self):
        # La migración corre en cada `init`. Si el relleno se aplicara siempre,
        # un cliente al que le cambiaste el plan volvería a su periodicidad.
        cid = self.cliente(plan="custom", estado_suscripcion="activa", cadencia_dias=10)
        db.inicializar(self.ruta)
        fila = db.fila(self.con, "SELECT * FROM clientes WHERE id = ?", (cid,))
        self.assertEqual(fila["plan"], "custom")
        self.assertEqual(fila["cadencia_dias"], 10)


class TestSospechosasEnElBorrador(CasoBase):
    """Antes: el aviso de "este competidor no devolvió nada" vivía solo en el
    log de la corrida. El log se lee una vez y se olvida; lo que se lee antes
    de enviar es el borrador. Artelec devolvía cero anuncios teniendo ~52
    activos, y el reporte llegó a recomendar aprovechar ese hueco inexistente.
    """

    def setUp(self):
        super().setUp()
        self.cli = self.cliente()
        self.competidor(self.cli, "Artelec")

    def _corrida_con_sospechosa(self, competidor="Artelec"):
        db.insertar(self.con, "corridas_recoleccion", {
            "estado": "parcial", "fuente": "demo",
            "terminada_en": util.ahora_iso(),
            "resumen_json": db.json_o_nada({
                "totales": {"nuevos": 0},
                "sospechosas": [{"competidor": competidor, "plataforma": "meta",
                                 "motivo": "nunca devolvió un solo anuncio"}],
            }),
        })
        self.con.commit()

    def test_el_aviso_llega_al_borrador_exportado(self):
        self._corrida_con_sospechosa()
        rid = db.insertar(self.con, "reportes_generados", {
            "cliente_id": self.cli, "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-01-07", "asunto": "x", "borrador_md": "cuerpo",
        })
        self.con.commit()
        texto = flujo.exportar(self.con, rid).read_text(encoding="utf-8")
        self.assertIn("REVISAR ANTES DE ENVIAR", texto)
        self.assertIn("Artelec", texto)

    def test_solo_avisa_de_los_competidores_de_ESE_cliente(self):
        # Una corrida toca a todos los clientes a la vez: el borrador de uno
        # no puede mostrar los problemas de los competidores de otro.
        self._corrida_con_sospechosa(competidor="Competidor De Otro Cliente")
        rid = db.insertar(self.con, "reportes_generados", {
            "cliente_id": self.cli, "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-01-07", "asunto": "x", "borrador_md": "cuerpo",
        })
        self.con.commit()
        texto = flujo.exportar(self.con, rid).read_text(encoding="utf-8")
        self.assertNotIn("REVISAR ANTES DE ENVIAR", texto)

    def test_sin_sospechosas_el_encabezado_queda_limpio(self):
        rid = db.insertar(self.con, "reportes_generados", {
            "cliente_id": self.cli, "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-01-07", "asunto": "x", "borrador_md": "cuerpo",
        })
        self.con.commit()
        texto = flujo.exportar(self.con, rid).read_text(encoding="utf-8")
        self.assertNotIn("REVISAR ANTES DE ENVIAR", texto)
