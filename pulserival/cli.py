"""Interfaz de comandos de PulseRival.

Todo se opera desde acá. Los comandos están en español y hacen una sola cosa:

  init                         crea la base de datos
  clientes agregar|lista       administrar clientes
  competidores agregar|lista   administrar competidores por cliente
  recolectar                   traer anuncios (paso 1 y 2)
  reporte generar              armar el borrador con IA (paso 3)
  reporte exportar             dejar el borrador en un .md para editar
  reporte registrar            guardar tu versión final + el diff (Fase 2)
  reporte enviar               mandar el reporte al cliente (paso 4)
  reporte lista                ver el estado de los reportes
  ciclo                        recolectar + generar + exportar (lo que corre el cron)
  feedback agregar             anotar qué preguntó o destacó el cliente
  dataset                      exportar el dataset de ediciones y ver métricas
  demo                         probar todo el flujo sin claves ni costo
  costos                       cuánto se gastó en IA
  presupuesto                  proyección del gasto mensual antes de gastarlo
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, db, pipeline, presupuesto as presupuesto_mod, util
from .ia import Presupuesto, PresupuestoExcedido, ProveedorError
from .reporte import generar as generar_mod
from .reporte import validar as validar_mod
from .revision import flujo


# ── utilidades de impresión ──────────────────────────────────────────
def ok(msg: str) -> None:
    print(f"✓ {msg}")


def aviso(msg: str) -> None:
    print(f"! {msg}")


def error(msg: str) -> int:
    print(f"✗ {msg}", file=sys.stderr)
    return 1


def tabla(filas: list, columnas: list[str]) -> None:
    if not filas:
        print("  (no hay nada todavía)")
        return
    def celda(fila, col) -> str:
        valor = dict(fila).get(col)
        return "" if valor is None else str(valor)

    datos = [[celda(f, c) for c in columnas] for f in filas]
    anchos = [max(len(c), *(len(d[i]) for d in datos)) for i, c in enumerate(columnas)]
    print("  " + " │ ".join(c.upper().ljust(anchos[i]) for i, c in enumerate(columnas)))
    print("  " + "─┼─".join("─" * a for a in anchos))
    for d in datos:
        print("  " + " │ ".join(v.ljust(anchos[i]) for i, v in enumerate(d)))


# ── comandos ─────────────────────────────────────────────────────────
def cmd_init(args) -> int:
    ruta = db.inicializar()
    ok(f"Base de datos lista en {ruta}")
    print("  Siguiente paso: python -m pulserival.cli clientes agregar --ayuda-datos")
    return 0


def cmd_clientes(args) -> int:
    with db.sesion() as con:
        if args.accion == "agregar":
            if not args.empresa or not args.email:
                return error("Para agregar un cliente hacen falta --empresa y --email")
            periodicidad = args.periodicidad or "semanal"
            cid = db.insertar(con, "clientes", {
                "nombre_empresa": args.empresa,
                "contacto_nombre": args.contacto,
                "contacto_email": args.email,
                "contacto_whatsapp": args.whatsapp,
                "periodicidad": periodicidad,
                "dia_envio": args.dia or "martes",
                "industria": args.industria,
                "notas": args.notas,
            })
            ok(f"Cliente #{cid}: {args.empresa} ({periodicidad})")
            return 0
        if args.accion == "editar":
            if args.id is None:
                return error("Falta --id: decime qué cliente editar (los ves con: clientes lista)")
            # Solo se escriben los campos que pasaste: sin --periodicidad no se
            # toca la cadencia del cliente.
            cambios = {k: v for k, v in {
                "contacto_email": args.email, "contacto_nombre": args.contacto,
                "contacto_whatsapp": args.whatsapp, "periodicidad": args.periodicidad,
                "dia_envio": args.dia, "industria": args.industria, "notas": args.notas,
                "activo": None if args.activo is None else int(args.activo),
            }.items() if v is not None}
            if not cambios:
                return error("No pasaste ningún campo para cambiar")
            db.actualizar(con, "clientes", args.id, cambios)
            ok(f"Cliente #{args.id} actualizado: {', '.join(cambios)}")
            return 0
        tabla(db.filas(con, "SELECT * FROM clientes ORDER BY id"),
              ["id", "nombre_empresa", "contacto_email", "periodicidad", "industria", "activo"])
        return 0


def cmd_competidores(args) -> int:
    with db.sesion() as con:
        if args.accion == "agregar":
            if not args.cliente or not args.nombre:
                return error("Para agregar un competidor hacen falta --cliente y --nombre")
            if not (args.meta_pagina or args.meta_consulta or args.google_dominio or args.google_anunciante):
                return error("Hace falta al menos uno: --meta-pagina, --meta-consulta, "
                             "--google-dominio o --google-anunciante")
            cid = db.insertar(con, "competidores_seguidos", {
                "cliente_id": args.cliente,
                "nombre": args.nombre,
                "meta_pagina_url": args.meta_pagina,
                "meta_consulta": args.meta_consulta,
                "google_dominio": args.google_dominio,
                "google_anunciante": args.google_anunciante,
                "prioridad": 2 if args.prioridad is None else args.prioridad,
                "notas": args.notas,
            })
            ok(f"Competidor #{cid}: {args.nombre} (cliente {args.cliente})")
            return 0
        if args.accion == "editar":
            if args.id is None:
                return error("Falta --id: decime qué competidor editar (los ves con: competidores lista)")
            # Sin --prioridad no se toca la prioridad que ya tenía.
            cambios = {k: v for k, v in {
                "meta_pagina_url": args.meta_pagina, "meta_consulta": args.meta_consulta,
                "google_dominio": args.google_dominio, "google_anunciante": args.google_anunciante,
                "prioridad": args.prioridad, "notas": args.notas,
                "activo": None if args.activo is None else int(args.activo),
            }.items() if v is not None}
            if not cambios:
                return error("No pasaste ningún campo para cambiar")
            db.actualizar(con, "competidores_seguidos", args.id, cambios)
            ok(f"Competidor #{args.id} actualizado: {', '.join(cambios)}")
            return 0
        sql = "SELECT * FROM competidores_seguidos"
        params: tuple = ()
        if args.cliente:
            sql += " WHERE cliente_id = ?"
            params = (args.cliente,)
        tabla(db.filas(con, sql + " ORDER BY cliente_id, prioridad", params),
              ["id", "cliente_id", "nombre", "meta_pagina_url", "meta_consulta",
               "google_dominio", "prioridad", "activo"])
        return 0


def cmd_recolectar(args) -> int:
    with db.sesion() as con:
        corrida = pipeline.recolectar(
            con, cliente_id=args.cliente, modo=args.modo, limite=args.limite,
            plataformas=tuple(args.plataformas.split(",")) if args.plataformas else pipeline.PLATAFORMAS,
        )
    t = corrida.totales()
    ok(f"Corrida #{corrida.id}: {t['nuevos']} nuevos · {t['cambiados']} cambiados · "
       f"{t['pausados']} se cayeron · {t['continuan']} siguen igual")
    for r in corrida.resultados:
        print(f"    {r.competidor} [{r.plataforma}]: {r.resumen()}")
    for s in corrida.sospechosas:
        aviso(f"REVISAR · {s['competidor']} [{s['plataforma']}]: {s['motivo']}")
    for s in corrida.saltados:
        aviso(f"{s['competidor']} [{s['plataforma']}]: {s['motivo']}")
    for e in corrida.errores:
        error(f"{e['competidor']} [{e['plataforma']}]: {e['error']}")
    return 0


def cmd_reporte(args) -> int:
    with db.sesion() as con:
        if args.accion == "generar":
            cliente = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (args.cliente,))
            if not cliente:
                return error(f"No existe el cliente {args.cliente}")
            inicio, fin = (args.desde, args.hasta) if args.desde and args.hasta else \
                util.periodo(cliente["periodicidad"] or "semanal")
            try:
                rep = generar_mod.generar(con, cliente, inicio, fin,
                                          presupuesto=Presupuesto(), regenerar=args.regenerar)
            except PresupuestoExcedido as e:
                return error(str(e))
            except ProveedorError as e:
                return error(f"La IA no pudo generar el reporte: {e}")
            if rep.get("ya_existia"):
                aviso(f"Ya existía el reporte #{rep['reporte_id']} para ese periodo "
                      f"(estado: {rep['estado']}). Usá --regenerar para rehacerlo.")
                return 0
            ok(f"Reporte #{rep['reporte_id']} para {cliente['nombre_empresa']} "
               f"({inicio} al {fin}) · {rep['proveedor']}/{rep['modelo']} · "
               f"${rep['costo_usd']:.4f}")
            print("  " + validar_mod.formatear(rep["validacion"]).replace("\n", "\n  "))
            archivo = flujo.exportar(con, rep["reporte_id"])
            print(f"  Borrador para editar: {archivo}")
            return 0

        if args.accion == "exportar":
            archivo = flujo.exportar(con, args.id)
            ok(f"Borrador exportado: {archivo}")
            return 0

        if args.accion == "registrar":
            res = flujo.registrar_final(
                con, args.id, ruta=Path(args.archivo) if args.archivo else None,
                etiqueta=args.etiqueta, razon=args.razon,
                autoetiquetar=not args.sin_autoetiqueta,
            )
            pct = res["similitud"] * 100
            ok(f"Edición #{res['edicion_id']} registrada · se mantuvo {pct:.1f}% del borrador "
               f"· etiqueta: {res['etiqueta']}")
            if not res["cambios"]:
                print("  No cambiaste nada: buena señal para la Fase 3.")
            return 0

        if args.accion == "enviar":
            from .entrega import EnvioError, enviar_reporte
            try:
                res = enviar_reporte(con, args.id, simular=args.simular,
                                     destinatario=args.para, permitir_borrador=args.forzar)
            except EnvioError as e:
                return error(str(e))
            if res["enviado"]:
                ok(f"Enviado a {res['para']} por {res['canal']}")
            else:
                ok(f"Simulado (no se envió nada). Archivo: {res['archivo']}")
            return 0

        if args.accion == "ver":
            rep = db.fila(con, "SELECT * FROM reportes_generados WHERE id = ?", (args.id,))
            if not rep:
                return error(f"No existe el reporte {args.id}")
            print(rep["final_md"] or rep["borrador_md"])
            return 0

        tabla(db.filas(con,
              "SELECT r.id, c.nombre_empresa AS cliente, r.periodo_inicio, r.periodo_fin, "
              "r.estado, r.modelo_ia, round(r.costo_usd,4) AS costo_usd, r.enviado_en "
              "FROM reportes_generados r JOIN clientes c ON c.id = r.cliente_id ORDER BY r.id DESC"),
              ["id", "cliente", "periodo_inicio", "periodo_fin", "estado", "modelo_ia",
               "costo_usd", "enviado_en"])
        return 0


def cmd_ciclo(args) -> int:
    with db.sesion() as con:
        res = pipeline.ciclo_completo(con, cliente_id=args.cliente, modo=args.modo)
    t = res["totales"]
    ok(f"Corrida #{res['corrida_id']}: {t['nuevos']} nuevos · {t['cambiados']} cambiados · "
       f"{t['pausados']} se cayeron · costo IA ${res['costo_ia_usd']}")
    for r in res["reportes"]:
        if "error" in r:
            error(f"{r['cliente']}: {r['error']}")
            continue
        if "omitido" in r:
            print(f"    {r['cliente']}: {r['omitido']}")
            continue
        val = r.get("validacion") or {}
        estado = "APROBADO" if val.get("aprobado") else "REVISAR"
        marca = " (ya existía)" if r.get("ya_existia") else ""
        print(f"    {r['cliente']}: reporte #{r['reporte_id']}{marca} · {estado} "
              f"· {r.get('archivo','')}")
    for e in res["errores"]:
        error(f"{e['competidor']} [{e['plataforma']}]: {e['error']}")
    for s in res.get("sospechosas", []):
        aviso(f"REVISAR · {s['competidor']} [{s['plataforma']}]: {s['motivo']}")
    for s in res["saltados"]:
        aviso(f"{s['competidor']} [{s['plataforma']}]: {s['motivo']}")
    return 0


def cmd_feedback(args) -> int:
    with db.sesion() as con:
        if args.accion == "agregar":
            if not args.cliente or not args.texto:
                return error("Para anotar feedback hacen falta --cliente y --texto")
            fid = db.insertar(con, "feedback_cliente", {
                "cliente_id": args.cliente, "reporte_id": args.reporte, "tipo": args.tipo,
                "canal": args.canal, "texto": args.texto, "seccion": args.seccion,
            })
            ok(f"Feedback #{fid} guardado ({args.tipo})")
            return 0
        tabla(db.filas(con, "SELECT f.id, c.nombre_empresa AS cliente, f.tipo, f.canal, "
                            "f.seccion, f.texto, f.creado_en FROM feedback_cliente f "
                            "JOIN clientes c ON c.id = f.cliente_id ORDER BY f.id DESC"),
              ["id", "cliente", "tipo", "canal", "seccion", "texto"])
        return 0


def cmd_dataset(args) -> int:
    with db.sesion() as con:
        destino = flujo.exportar_dataset(con)
        m = flujo.metricas(con)
    ok(f"Dataset de ediciones: {destino}")
    print(json.dumps(m, ensure_ascii=False, indent=2))
    if m.get("reportes_revisados", 0) and not m.get("listo_para_fase_3"):
        print("  Todavía no conviene automatizar el envío: seguí registrando ediciones.")
    return 0


def cmd_costos(args) -> int:
    with db.sesion() as con:
        filas = db.filas(con,
            "SELECT tarea, proveedor, modelo, COUNT(*) AS llamadas, "
            "SUM(tokens_entrada) AS entrada, SUM(tokens_salida) AS salida, "
            "round(SUM(costo_usd), 5) AS costo_usd, SUM(1-exito) AS fallos "
            "FROM uso_ia GROUP BY tarea, proveedor, modelo ORDER BY costo_usd DESC")
        total = db.fila(con, "SELECT round(SUM(costo_usd),5) AS t FROM uso_ia")
    tabla(filas, ["tarea", "proveedor", "modelo", "llamadas", "entrada", "salida", "costo_usd", "fallos"])
    print(f"\n  Total gastado en IA: ${(total['t'] if total and total['t'] else 0)}")
    return 0


def cmd_presupuesto(args) -> int:
    """Proyecta el gasto mensual de scrapers con los clientes ya cargados."""
    with db.sesion() as con:
        p = presupuesto_mod.proyectar(con, limite_por_competidor=args.limite)
    print("  " + presupuesto_mod.formatear(p).replace("\n", "\n  "))
    return 0


def cmd_demo(args) -> int:
    """Corre el flujo completo con datos de ejemplo, sin claves ni costo."""
    import os

    ruta = config.DIR_DATOS / "demo.db"
    if ruta.exists() and not args.conservar:
        ruta.unlink()
    os.environ["PULSERIVAL_DB"] = str(ruta)
    config.cargar_env()
    os.environ["PULSERIVAL_DB"] = str(ruta)
    db.inicializar(ruta)

    print("── 1. Cliente piloto y competidores de ejemplo ──")
    with db.sesion(ruta) as con:
        cliente_id = db.insertar(con, "clientes", {
            "nombre_empresa": "Gimnasio Fuerza Tica",
            "contacto_nombre": "Ana Rojas",
            "contacto_email": "ana@fuerzatica.example.com",
            "periodicidad": "semanal",
            "industria": "gimnasios y bienestar",
            "notas": "Dos sedes: Escazú y Curridabat. Su ticket promedio es ¢22.000/mes. "
                     "Le interesa sobre todo el precio de la competencia y las promos de matrícula.",
        })
        for nombre, extra in [
            ("Vital Gym CR", {"meta_consulta": "Vital Gym CR", "google_dominio": "vitalgymcr.example.com", "prioridad": 1}),
            ("Club Atlas Escazú", {"meta_consulta": "Club Atlas Escazú", "prioridad": 2}),
        ]:
            db.insertar(con, "competidores_seguidos", {"cliente_id": cliente_id, "nombre": nombre, **extra})
    ok("Cliente y 2 competidores creados")

    for semana, etiqueta in ((1, "primera corrida"), (2, "una semana después")):
        os.environ["PULSERIVAL_DEMO_SEMANA"] = str(semana)
        print(f"\n── 2.{semana} Recolección ({etiqueta}) ──")
        with db.sesion(ruta) as con:
            corrida = pipeline.recolectar(con, modo="demo", disparada_por="demo")
        t = corrida.totales()
        ok(f"{t['nuevos']} nuevos · {t['cambiados']} cambiados · {t['pausados']} se cayeron "
           f"· {t['continuan']} siguen igual")

    print("\n── 3. Borrador con IA (o con reglas si no hay claves) ──")
    with db.sesion(ruta) as con:
        cliente = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (cliente_id,))
        inicio, fin = util.periodo("semanal")
        rep = generar_mod.generar(con, cliente, inicio, fin, presupuesto=Presupuesto())
        ok(f"Reporte #{rep['reporte_id']} con {rep['proveedor']}/{rep['modelo']} "
           f"· ${rep['costo_usd']:.4f}")
        print("  " + validar_mod.formatear(rep["validacion"]).replace("\n", "\n  "))
        archivo = flujo.exportar(con, rep["reporte_id"])
        print(f"  Borrador editable: {archivo}")

        print("\n── 4. Simulo tu edición y registro el diff (dataset Fase 2) ──")
        final = (rep["borrador_md"] or "") + (
            "\n\n_Nota del editor: hablé con Ana y su prioridad de este mes es retención, "
            "no captación._\n"
        )
        res = flujo.registrar_final(con, rep["reporte_id"], final_md=final,
                                    etiqueta="contexto_cliente",
                                    razon="Agregué contexto que solo se sabe por conversación con la clienta",
                                    autoetiquetar=False)
        ok(f"Edición #{res['edicion_id']} registrada · se mantuvo {res['similitud']*100:.1f}% del borrador")

        print("\n── 5. Entrega (simulada, no se manda ningún correo) ──")
        from .entrega import enviar_reporte
        envio = enviar_reporte(con, rep["reporte_id"], simular=True)
        ok(f"Email armado en {envio['archivo']}")
        print(f"  Versión WhatsApp: {envio['archivo'].replace('.html', '-whatsapp.txt')}")

        print("\n── 6. Dataset y métricas ──")
        destino = flujo.exportar_dataset(con)
        ok(f"{destino}")
        print(json.dumps(flujo.metricas(con), ensure_ascii=False, indent=2))

    print(f"\nListo. La base de la demo quedó en {ruta} (borrala cuando quieras).")
    print("Abrí el HTML en el navegador para ver el reporte como lo recibe el cliente.")
    return 0


def cmd_fuentes(args) -> int:
    """Inspecciona la salida cruda de un scraper para ajustar el mapeo del YAML."""
    datos = json.loads(Path(args.archivo).read_text(encoding="utf-8"))
    items = datos if isinstance(datos, list) else [datos]
    if not items:
        return error("El archivo no tiene items")
    claves: dict[str, int] = {}
    for it in items:
        if isinstance(it, dict):
            for k in it:
                claves[k] = claves.get(k, 0) + 1
    print(f"  {len(items)} items · campos encontrados:")
    for k, n in sorted(claves.items(), key=lambda x: (-x[1], x[0])):
        muestra = util.recortar(str(items[0].get(k)), 60)
        print(f"    {k:32} ({n}/{len(items)})  ej: {muestra}")
    print("\n  Copiá los nombres relevantes a config/fuentes.yaml, sección 'mapeo'.")
    return 0


# ── armado del parser ────────────────────────────────────────────────
def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pulserival",
        description="PulseRival · reportes de anuncios de la competencia (Costa Rica)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Primer uso:  python -m pulserival.cli demo",
    )
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("init", help="crear la base de datos").set_defaults(func=cmd_init)

    # clientes
    c = sub.add_parser("clientes", help="administrar clientes")
    c.add_argument("accion", nargs="?", default="lista", choices=["lista", "agregar", "editar"])
    c.add_argument("--id", type=int)
    c.add_argument("--empresa")
    c.add_argument("--contacto")
    c.add_argument("--email")
    c.add_argument("--whatsapp")
    c.add_argument("--periodicidad", choices=["semanal", "mensual"],
                   help="semanal (por defecto al agregar); al editar, solo cambia si la pasás")
    c.add_argument("--dia", help="día preferido de entrega (martes por defecto al agregar)")
    c.add_argument("--industria")
    c.add_argument("--notas", help="contexto del cliente: mejora mucho el reporte")
    c.add_argument("--activo", type=int, choices=[0, 1])
    c.set_defaults(func=cmd_clientes)

    # competidores
    k = sub.add_parser("competidores", help="administrar competidores por cliente")
    k.add_argument("accion", nargs="?", default="lista", choices=["lista", "agregar", "editar"])
    k.add_argument("--id", type=int)
    k.add_argument("--cliente", type=int)
    k.add_argument("--nombre")
    k.add_argument("--meta-pagina", dest="meta_pagina", help="URL de la página de Facebook")
    k.add_argument("--meta-consulta", dest="meta_consulta", help="término de búsqueda en la Ad Library")
    k.add_argument("--google-dominio", dest="google_dominio", help="dominio del anunciante")
    k.add_argument("--google-anunciante", dest="google_anunciante")
    k.add_argument("--prioridad", type=int,
                   help="1 = el que más importa (2 por defecto al agregar)")
    k.add_argument("--notas")
    k.add_argument("--activo", type=int, choices=[0, 1])
    k.set_defaults(func=cmd_competidores)

    # recolectar
    r = sub.add_parser("recolectar", help="traer anuncios de los scrapers")
    r.add_argument("--cliente", type=int, help="solo este cliente")
    r.add_argument("--modo", default="auto", choices=["auto", "demo", "apify"],
                   help="auto usa Apify si hay token; demo usa datos de ejemplo")
    r.add_argument("--plataformas", help="meta,google (por defecto las dos)")
    r.add_argument("--limite", type=int, default=40, help="máximo de anuncios por competidor")
    r.set_defaults(func=cmd_recolectar)

    # reporte
    g = sub.add_parser("reporte", help="generar, revisar y enviar reportes")
    g.add_argument("accion", nargs="?", default="lista",
                   choices=["lista", "generar", "exportar", "registrar", "enviar", "ver"])
    g.add_argument("--id", type=int, help="id del reporte")
    g.add_argument("--cliente", type=int)
    g.add_argument("--desde")
    g.add_argument("--hasta")
    g.add_argument("--regenerar", action="store_true", help="rehacer el borrador del periodo")
    g.add_argument("--archivo", help="el .md editado (si no, se busca en borradores/)")
    g.add_argument("--etiqueta", choices=flujo.ETIQUETAS_VALIDAS, help="qué tipo de cambio hiciste")
    g.add_argument("--razon", help="en una línea, por qué lo cambiaste")
    g.add_argument("--sin-autoetiqueta", dest="sin_autoetiqueta", action="store_true")
    g.add_argument("--simular", action="store_true", help="no enviar, solo generar el archivo")
    g.add_argument("--para", help="enviar a otro correo (para probar)")
    g.add_argument("--forzar", action="store_true", help="enviar aunque siga en borrador")
    g.set_defaults(func=cmd_reporte)

    # ciclo (cron)
    y = sub.add_parser("ciclo", help="recolectar + generar + exportar (lo que corre el cron)")
    y.add_argument("--cliente", type=int)
    y.add_argument("--modo", default="auto", choices=["auto", "demo", "apify"])
    y.set_defaults(func=cmd_ciclo)

    # feedback
    f = sub.add_parser("feedback", help="anotar reacciones del cliente")
    f.add_argument("accion", nargs="?", default="lista", choices=["lista", "agregar"])
    f.add_argument("--cliente", type=int)
    f.add_argument("--reporte", type=int)
    f.add_argument("--tipo", choices=["pregunta", "destacado", "ignorado", "queja", "pedido"],
                   default="pregunta")
    f.add_argument("--canal", default="whatsapp")
    f.add_argument("--texto")
    f.add_argument("--seccion")
    f.set_defaults(func=cmd_feedback)

    sub.add_parser("dataset", help="exportar el dataset de ediciones (Fase 2)").set_defaults(func=cmd_dataset)
    sub.add_parser("costos", help="cuánto se gastó en IA").set_defaults(func=cmd_costos)

    b = sub.add_parser("presupuesto", help="cuánto va a costar al mes, antes de gastarlo")
    b.add_argument("--limite", type=int, default=40,
                   help="anuncios por competidor por corrida (el mismo de recolectar)")
    b.set_defaults(func=cmd_presupuesto)

    d = sub.add_parser("demo", help="probar el flujo completo sin claves ni costo")
    d.add_argument("--conservar", action="store_true", help="no borrar la base de la demo anterior")
    d.set_defaults(func=cmd_demo)

    s = sub.add_parser("fuentes", help="inspeccionar la salida cruda de un scraper")
    s.add_argument("accion", nargs="?", default="inspeccionar", choices=["inspeccionar"])
    s.add_argument("--archivo", required=True, help="JSON guardado en datos/crudo/")
    s.set_defaults(func=cmd_fuentes)

    return p


def main(argv: list[str] | None = None) -> int:
    config.cargar_env()
    args = construir_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        return error("Cancelado")
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        return error(str(e))


if __name__ == "__main__":
    sys.exit(main())
