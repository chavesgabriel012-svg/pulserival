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
  aplicar-config               cargar clientes y competidores desde config/clientes.yaml
  prueba-scraper               llamar al scraper real una vez y ver qué devuelve
  ciclo                        recolectar + generar + exportar (lo que corre el cron)
  feedback agregar             anotar qué preguntó o destacó el cliente
  dataset                      exportar el dataset de ediciones y ver métricas
  demo                         probar todo el flujo sin claves ni costo
  costos                       cuánto se gastó en IA
  diagnostico                  probar los proveedores de IA y ver cuál responde
  presupuesto                  proyección del gasto mensual antes de gastarlo
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (config, db, pipeline, planes,
               presupuesto as presupuesto_mod, sincronizar, util)
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
            plan = args.plan or "semanal"
            datos_plan = planes.plan(plan)
            if not datos_plan:
                return error(f"El plan '{plan}' no está en config/planes.yaml. "
                             f"Disponibles: {', '.join(planes.PLANES_VALIDOS)}")
            # La periodicidad sale del plan salvo que se pida otra: el enum
            # viejo sigue existiendo y tiene que quedar coherente.
            periodicidad = args.periodicidad or datos_plan.get("periodicidad") or "semanal"
            estado = args.estado or ("prueba_pendiente" if plan == "prueba"
                                     else "pendiente_pago")
            if estado not in planes.ESTADOS_VALIDOS:
                return error(f"Estado '{estado}' inválido. "
                             f"Válidos: {', '.join(planes.ESTADOS_VALIDOS)}")
            cid = db.insertar(con, "clientes", {
                "nombre_empresa": args.empresa,
                "contacto_nombre": args.contacto,
                "contacto_email": args.email,
                "contacto_whatsapp": args.whatsapp,
                "periodicidad": periodicidad,
                "plan": plan,
                "estado_suscripcion": estado,
                "cadencia_dias": args.cadencia_dias,
                "precio_mensual_usd": args.precio,
                "pago_proveedor": args.pago_proveedor,
                "pago_referencia": args.pago_referencia,
                "dia_envio": args.dia or "martes",
                "industria": args.industria,
                "notas": args.notas,
            })
            ok(f"Cliente #{cid}: {args.empresa} · plan {plan} · {estado}")
            if estado == "pendiente_pago":
                print(f"    No se le van a generar reportes hasta confirmar el pago:")
                print(f"      python3 -m pulserival.cli clientes activar --id {cid} "
                      f"--referencia \"<comprobante>\"")
            return 0
        if args.accion == "activar":
            # El paso manual del flujo híbrido: usted confirma el pago en el
            # panel de Tilopay/Onvopay y acá deja constancia de cuál fue.
            if args.id is None:
                return error("Falta --id: qué cliente activar (los ves con: clientes lista)")
            fila = db.fila(con, "SELECT * FROM clientes WHERE id = ?", (args.id,))
            if not fila:
                return error(f"No existe el cliente {args.id}")
            cambios = {"estado_suscripcion": "activa", "activo": 1}
            if args.pago_referencia:
                cambios["pago_referencia"] = args.pago_referencia
            if args.pago_proveedor:
                cambios["pago_proveedor"] = args.pago_proveedor
            if args.precio is not None:
                cambios["precio_mensual_usd"] = args.precio
            db.actualizar(con, "clientes", args.id, cambios)
            ok(f"Cliente #{args.id} ({fila['nombre_empresa']}) activo · "
               f"plan {db.valor(fila, 'plan', '?')}")
            if not args.pago_referencia:
                aviso("Sin --referencia no queda registrado contra qué pago se activó. "
                      "Conviene anotar el comprobante o el id de la suscripción.")
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
              ["id", "nombre_empresa", "plan", "estado_suscripcion", "cadencia_dias",
               "contacto_email", "activo"])
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
    return _salida_segun_corrida(corrida.errores, corrida.resultados)


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
        res = pipeline.ciclo_completo(con, cliente_id=args.cliente, modo=args.modo,
                                      limite=args.limite, solo_reporte=args.solo_reporte)
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
        print(f"    {r['cliente']}: reporte #{r['reporte_id']}{marca} · {estado}")
        if r.get("archivo"):
            print(f"       borrador para editar: {r['archivo']}")
        if r.get("previsualizacion"):
            print(f"       correo armado:        {r['previsualizacion']}")
        if r.get("previsualizacion_error"):
            aviso(f"no se pudo armar la previa del correo: {r['previsualizacion_error']}")
    for e in res["errores"]:
        error(f"{e['competidor']} [{e['plataforma']}]: {e['error']}")
    for s in res.get("sospechosas", []):
        aviso(f"REVISAR · {s['competidor']} [{s['plataforma']}]: {s['motivo']}")
    for s in res["saltados"]:
        aviso(f"{s['competidor']} [{s['plataforma']}]: {s['motivo']}")
    return _salida_segun_corrida(res["errores"], res["fuentes_ok"])


def _salida_segun_corrida(errores: list, fuentes_ok) -> int:
    """Código de salida de una recolección.

    Importa más de lo que parece: el cron de GitHub solo se pone en rojo (y te
    manda el correo de aviso) si el comando sale con código distinto de cero.
    Si todas las fuentes fallaron y aun así saliéramos con 0, tendrías un
    workflow en verde recolectando nada durante semanas, y te enterarías el
    día que un cliente pregunte por su reporte.
    """
    if errores and not fuentes_ok:
        return error(f"Ninguna fuente funcionó ({len(errores)} errores). "
                     "No se recolectó nada en esta corrida.")
    if errores:
        aviso(f"Corrida parcial: {len(errores)} fuente(s) fallaron, el resto sí trajo datos.")
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


def cmd_aplicar_config(args) -> int:
    """Crea o actualiza clientes y competidores desde config/clientes.yaml."""
    ruta = Path(args.archivo) if args.archivo else None
    try:
        with db.sesion() as con:
            resumen = sincronizar.aplicar(con, ruta)
    except sincronizar.ConfigInvalida as e:
        return error(str(e))
    ok("Configuración aplicada")
    print("  " + sincronizar.formatear(resumen).replace("\n", "\n  "))
    return 0


def cmd_prueba_scraper(args) -> int:
    """Llama al scraper real una vez y muestra qué devuelve.

    Es la prueba que conviene hacer ANTES de cargar un cliente: confirma que
    hay anuncios para ese competidor, que el token funciona y que los campos
    del actor siguen coincidiendo con el mapeo de config/fuentes.yaml.
    Cuesta centavos porque el límite es chico a propósito.
    """
    from .fuentes import FuenteError, obtener_fuente

    competidor = {
        "id": 0,
        "nombre": args.consulta,
        "meta_consulta": args.consulta,
        "meta_pagina_url": args.pagina,
        "meta_pagina_id": getattr(args, "pagina_id", None),
        "google_dominio": args.dominio or args.consulta,
    }
    print(f"  Consultando {args.plataforma} · '{args.consulta}' · máximo {args.limite} anuncios")
    try:
        fuente = obtener_fuente(args.plataforma, args.modo)
        anuncios = fuente.traer(competidor, limite=args.limite)
    except FuenteError as e:
        return error(str(e))

    print(f"  Fuente: {fuente.nombre}")
    print(f"  Anuncios devueltos: {len(anuncios)}")
    if not anuncios:
        aviso("No devolvió anuncios. Puede ser que ese anunciante no esté pautando ahora, "
              "o que la consulta no coincida. Verificá a mano en la biblioteca pública.")
        return 0

    vacios = [a for a in anuncios if not a.texto and not a.titulo]
    sin_fecha = [a for a in anuncios if not a.fecha_inicio]
    sin_link = [a for a in anuncios if not a.url_anuncio]
    print("\n  Calidad del mapeo (config/fuentes.yaml):")
    for etiqueta, faltantes in (("sin texto ni título", vacios),
                                ("sin fecha de inicio", sin_fecha),
                                ("sin link a la ficha pública", sin_link)):
        marca = "✓" if not faltantes else "!"
        print(f"    {marca} {etiqueta}: {len(faltantes)}/{len(anuncios)}")
    if vacios:
        aviso("Hay anuncios sin contenido: probablemente cambiaron los nombres de los campos "
              "del actor. Revisá datos/crudo/ con: fuentes inspeccionar --archivo ...")

    print("\n  Primeros anuncios tal como los guardaría el sistema:")
    for a in anuncios[: args.mostrar]:
        print(f"    ─ {a.anunciante or '(sin anunciante)'} · {a.tipo_creativo or '?'} "
              f"· inicio {a.fecha_inicio or 'no informado'}")
        print(f"      título: {util.recortar(a.titulo, 90) or '(sin título)'}")
        print(f"      texto:  {util.recortar(a.texto, 140) or '(sin texto)'}")
        print(f"      link:   {util.recortar(a.link_destino, 80) or '(sin link)'}")
        print(f"      ficha:  {util.recortar(a.url_anuncio, 80) or '(sin ficha)'}")
        print(f"      huella: {a.huella()}")
    # El id del anunciante es lo que se copia a config/clientes.yaml cuando la
    # búsqueda por nombre de página falla. En Meta es el id numérico de la
    # página (meta_pagina_id); en Google, el del anunciante
    # (google_anunciante_id). Sin imprimirlo había que abrir la respuesta
    # cruda a mano para encontrarlo.
    ids = {}
    for a in anuncios:
        clave = (a.anunciante or "(sin anunciante)", str((a.metadata or {}).get("anunciante_id") or ""))
        ids[clave] = ids.get(clave, 0) + 1
    if ids:
        campo = "meta_pagina_id" if args.plataforma == "meta" else "google_anunciante_id"
        print(f"\n  Anunciantes en la respuesta (para {campo} en config/clientes.yaml):")
        for (nombre, ident), cuantos in sorted(ids.items(), key=lambda x: -x[1]):
            print(f"    {cuantos:>3} anuncio(s) · {nombre} · id: {ident or '(no informado)'}")
    crudos = sorted((config.DIR_DATOS / "crudo").glob("*.json"))
    if crudos:
        print(f"\n  Respuesta cruda guardada en: {crudos[-1]}")
    return 0


def cmd_mantenimiento(args) -> int:
    """Recalcula las huellas guardadas tras un cambio en cómo se calculan."""
    from . import mantenimiento

    with db.sesion() as con:
        res = mantenimiento.recalcular_huellas(con, aplicar=not args.simular)
    ok(f"{res['revisados']} anuncios revisados · {res['huellas_actualizadas']} huellas "
       f"actualizadas · {res['duplicados_fusionados']} duplicados fusionados"
       + ("  (simulación, no se guardó nada)" if args.simular else ""))
    return 0


def cmd_diagnostico(args) -> int:
    """Prueba cada proveedor de IA con una llamada mínima y dice cuál responde."""
    from .ia.router import diagnostico

    filas = diagnostico()
    tabla(filas, ["proveedor", "modelo", "estado", "detalle"])
    rotos = [f for f in filas if f["estado"] != "ok" and f["proveedor"] != "stub"]
    if rotos:
        aviso(f"{len(rotos)} proveedor(es) no responden. Con todos caídos el reporte sale "
              "sin interpretación, solo con los conteos.")
        return 1
    ok("Todos los proveedores de IA responden.")
    return 0


def cmd_modelos(args) -> int:
    """Lista los modelos que cada llave puede usar HOY y marca los del YAML.

    Existe porque Google y Groq retiran modelos sin avisar: un reporte salió
    escrito por el stub porque los cuatro modelos de la cadena habían dejado
    de existir, con config/modelos.yaml sin tocar.
    """
    from .ia.base import ProveedorError
    from .ia.router import _proveedor as _proveedor_ia
    from .ia.router import proveedores_configurados

    configurados = proveedores_configurados()
    problemas = 0
    verificados = 0
    for nombre in sorted(configurados):
        pedidos = configurados[nombre]
        try:
            prov = _proveedor_ia(nombre)
        except KeyError:
            aviso(f"{nombre}: no existe ese proveedor en el código")
            problemas += 1
            continue
        listar = getattr(prov, "listar_modelos", None)
        if not prov.disponible() or listar is None:
            print(f"  {nombre}: sin clave o sin listado disponible "
                  f"(pedidos en el YAML: {', '.join(sorted(pedidos)) or 'ninguno'})")
            continue
        try:
            disponibles = listar()
        except ProveedorError as e:
            aviso(f"{nombre}: {e}")
            problemas += 1
            continue
        verificados += 1
        faltan = sorted(m for m in pedidos if m not in disponibles)
        print(f"\n  {nombre}: {len(disponibles)} modelos disponibles")
        for m in disponibles:
            print(f"    {'<-- en el YAML' if m in pedidos else '':<15}{m}")
        if faltan:
            aviso(f"{nombre}: config/modelos.yaml pide modelos que ya no existen: "
                  f"{', '.join(faltan)}")
            problemas += len(faltan)
    if problemas:
        return 1
    if not verificados:
        # Sin llaves no se verificó nada. Decir "todo bien" aquí sería
        # exactamente el silencio que hizo falta detectar.
        aviso("No se pudo verificar ningún proveedor: faltan las llaves de API.")
        return 1
    ok("Todos los modelos de config/modelos.yaml existen.")
    return 0


def cmd_landing(args) -> int:
    """Genera la landing estática a partir de config/planes.yaml y landing.yaml."""
    from .landing import construir
    from .landing import pendientes

    destino = construir(Path(args.destino) if args.destino else None)
    ok(f"Landing generada: {destino}")
    print(f"    Ábrala en el navegador para revisarla, o súbala tal cual a "
          f"cualquier hosting de archivos.")
    faltantes = pendientes()
    for f in faltantes:
        aviso(f)
    if faltantes:
        print("\n    La página se genera igual: lo de arriba es lo que falta "
              "para que cobre sola.")
    return 0


def cmd_servidor(args) -> int:
    """Levanta la web (landing + alta + cobro simulado) para probarla local.

    En producción no se usa este comando: ahí lo levanta gunicorn, que es lo
    que aguanta varias peticiones a la vez. Este es el servidor de desarrollo
    de Flask y lo dice él mismo al arrancar.
    """
    try:
        from .web import crear_app
    except ImportError:
        return error("Falta Flask. Instalalo con: pip install -r requirements.txt")

    from .web.app import cobro_simulado

    if cobro_simulado():
        aviso("COBRO SIMULADO: confirmar en el checkout activa la cuenta sin "
              "cobrar nada. Para cambiarlo: PULSERIVAL_COBRO=real")
    ok(f"Servidor en http://{args.host}:{args.puerto}")
    print(f"    Base de datos: {config.ruta_db()}")
    crear_app().run(host=args.host, port=args.puerto, debug=args.debug)
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
    c.add_argument("accion", nargs="?", default="lista",
                   choices=["lista", "agregar", "editar", "activar"])
    c.add_argument("--id", type=int)
    c.add_argument("--empresa")
    c.add_argument("--contacto")
    c.add_argument("--email")
    c.add_argument("--whatsapp")
    c.add_argument("--plan", choices=list(planes.PLANES_VALIDOS),
                   help="plan vendido (ver config/planes.yaml). Por defecto: semanal")
    c.add_argument("--estado", choices=list(planes.ESTADOS_VALIDOS),
                   help="estado de la suscripción. Al agregar: pendiente_pago, "
                        "o prueba_pendiente si el plan es 'prueba'")
    c.add_argument("--cadencia-dias", dest="cadencia_dias", type=int,
                   help="cada cuántos días toca reporte (plan a la medida). "
                        "Le gana a --periodicidad")
    c.add_argument("--precio", type=float,
                   help="lo acordado con ESTE cliente, en USD por mes")
    c.add_argument("--pago-proveedor", dest="pago_proveedor",
                   choices=["tilopay", "onvopay", "manual"],
                   help="por dónde paga")
    c.add_argument("--referencia", dest="pago_referencia",
                   help="comprobante o id de suscripción contra el que se activa")
    c.add_argument("--periodicidad", choices=["semanal", "mensual"],
                   help="normalmente sale del plan; pasala solo para forzar otra")
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
    y.add_argument("--limite", type=int, default=40,
                   help="máximo de anuncios por competidor y plataforma (se paga por anuncio)")
    y.add_argument("--solo-reporte", dest="solo_reporte", action="store_true",
                   help="rehacer el reporte con lo que ya está en la base, sin volver a "
                        "llamar al scraper (no cuesta anuncios)")
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
    m = sub.add_parser("mantenimiento",
                       help="recalcular las huellas de los anuncios ya guardados")
    m.add_argument("accion", nargs="?", default="recalcular-huellas",
                   choices=["recalcular-huellas"])
    m.add_argument("--simular", action="store_true", help="mostrar qué haría, sin guardar")
    m.set_defaults(func=cmd_mantenimiento)

    sub.add_parser("diagnostico",
                   help="probar los proveedores de IA y ver cuál responde").set_defaults(
        func=cmd_diagnostico)

    sv = sub.add_parser("servidor", help="levantar la web para probarla local")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--puerto", type=int, default=5000)
    sv.add_argument("--debug", action="store_true")
    sv.set_defaults(func=cmd_servidor)

    lp = sub.add_parser("landing", help="generar la landing estática (planes + alta)")
    lp.add_argument("--destino", help="dónde escribir el index.html")
    lp.set_defaults(func=cmd_landing)

    sub.add_parser("modelos",
                   help="ver qué modelos de IA existen hoy y si el YAML pide alguno "
                        "que ya se retiró").set_defaults(func=cmd_modelos)

    b = sub.add_parser("presupuesto", help="cuánto va a costar al mes, antes de gastarlo")
    b.add_argument("--limite", type=int, default=40,
                   help="anuncios por competidor por corrida (el mismo de recolectar)")
    b.set_defaults(func=cmd_presupuesto)

    a = sub.add_parser("aplicar-config",
                       help="crear/actualizar clientes y competidores desde config/clientes.yaml")
    a.add_argument("--archivo", help="otro archivo YAML (por defecto config/clientes.yaml)")
    a.set_defaults(func=cmd_aplicar_config)

    t_ = sub.add_parser("prueba-scraper",
                        help="llamar al scraper real una vez y ver qué devuelve")
    t_.add_argument("--plataforma", default="meta", choices=["meta", "google"])
    t_.add_argument("--consulta", required=True, help="nombre del anunciante a buscar")
    t_.add_argument("--pagina", help="URL de la página de Facebook (más preciso que la consulta)")
    t_.add_argument("--pagina-id", dest="pagina_id",
                    help="id numérico de la página de Meta (lo más confiable: el actor "
                         "no tiene que resolver el nombre)")
    t_.add_argument("--dominio", help="dominio del anunciante, para Google")
    t_.add_argument("--limite", type=int, default=10, help="máximo de anuncios (cuesta por anuncio)")
    t_.add_argument("--mostrar", type=int, default=3, help="cuántos imprimir en pantalla")
    t_.add_argument("--modo", default="auto", choices=["auto", "demo", "apify"])
    t_.set_defaults(func=cmd_prueba_scraper)

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
