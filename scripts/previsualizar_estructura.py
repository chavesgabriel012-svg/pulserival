"""Vista previa de la ESTRUCTURA del reporte, sin gastar nada.

Usa los 30 anuncios reales guardados en tests/fixtures/ y el redactor sin
IA. Sirve para revisar el diseño del correo —tabla comparativa, anexo,
advertencias— sin llamar a Apify ni a ningún modelo.

    python3 scripts/previsualizar_estructura.py

Deja el HTML en salida/vista-previa-estructura.html.
"""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, '/home/user/pulserival')

ruta = Path(tempfile.mkdtemp()) / "preview.db"
os.environ["PULSERIVAL_DB"] = str(ruta)

from pulserival import db, priorizar, util
from pulserival.fuentes.apify import FuenteApify
from pulserival.reporte import datos as datos_mod, metricas, render, validar
from pulserival.ia.stub_proveedor import ProveedorStub

db.inicializar(ruta)
with db.sesion(ruta) as con:
    cli = db.insertar(con, "clientes", {
        "nombre_empresa": "Gollo", "contacto_email": "demo@ejemplo.invalid",
        "periodicidad": "semanal", "industria": "retail de electrodomésticos y muebles",
        "notas": "Vista previa de estructura.",
    })
    # Los 30 anuncios reales se reparten entre dos competidores de ejemplo,
    # solo para mostrar cómo se ve la comparación. En la corrida D real cada
    # competidor trae sus propios anuncios.
    comps = {}
    for nombre in ("Competidor A", "Competidor B"):
        cid = db.insertar(con, "competidores_seguidos", {
            "cliente_id": cli, "nombre": nombre, "meta_consulta": nombre,
            "google_dominio": "ejemplo.com", "prioridad": 1 if "A" in nombre else 2})
        comps[nombre] = db.fila(con, "SELECT * FROM competidores_seguidos WHERE id = ?", (cid,))

    meta = json.load(open("tests/fixtures/meta_anuncios_reales.json"))
    goog = json.load(open("tests/fixtures/google_anuncios_reales.json"))
    fm, fg = FuenteApify("meta", token="x"), FuenteApify("google", token="x")

    priorizar.conciliar(con, comps["Competidor A"], "meta", [fm.mapear(i) for i in meta[:9]])
    priorizar.conciliar(con, comps["Competidor A"], "google", [fg.mapear(i) for i in goog[:8]])
    priorizar.conciliar(con, comps["Competidor B"], "meta", [fm.mapear(i) for i in meta[9:]])
    priorizar.conciliar(con, comps["Competidor B"], "google", [fg.mapear(i) for i in goog[8:]])
    con.commit()

    inicio, fin = util.periodo("semanal")
    anuncios = datos_mod.clasificar(con, cli, inicio, fin)
    senales = metricas.por_competidor(anuncios, inicio, fin)
    conteo = datos_mod.conteo(anuncios)

    print("ANUNCIOS EN EL REPORTE:", len(anuncios), "· piezas reales:", 30)
    print("CONTEO:", conteo)
    print("\nSEÑALES QUE VERÁ EL CLIENTE:")
    print(metricas.resumir_para_prompt(senales))

    cuerpo = ProveedorStub()._redactar({
        "cliente": "Gollo", "periodo_inicio": inicio, "periodo_fin": fin, "anuncios": anuncios})

    rep = {
        "asunto": f"Competencia de Gollo · {fin}", "preheader": "vista previa de estructura",
        "cliente": "Gollo", "periodo_inicio": inicio, "periodo_fin": fin,
        "conteo": conteo, "senales": senales, "competidores": list(comps),
        "anuncios": anuncios, "cuerpo_md": cuerpo, "marca": "PulseRival",
        "miniaturas": True, "contacto_remitente": "reportes@pulserival.com",
    }
    destino = Path("/home/user/pulserival/salida/vista-previa-estructura.html")
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(render.email_html(rep), encoding="utf-8")
    print(f"\nHTML: {destino} ({destino.stat().st_size} bytes)")
    print("\nVALIDACIÓN:", validar.formatear(validar.validar(cuerpo, anuncios)))
