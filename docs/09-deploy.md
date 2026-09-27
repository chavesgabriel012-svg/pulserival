# 09 · Dónde y cómo se publica

## La decisión, y cómo cambió dos veces

Vale la pena dejar escrito el razonamiento, porque la conclusión se dio vuelta
cuando cambió lo que el producto tenía que hacer.

**Primera versión: "Vercel no, porque SQLite no persiste."** Correcto sobre
SQLite, equivocado sobre Vercel. El servidor Flask corre en Vercel sin cambiar
de framework; lo que no corre ahí es una base en disco.

**Segunda versión: "Vercel sí, depositando el alta como YAML en el
repositorio."** Funcionaba, porque un formulario de alta **solo escribe una
vez** y no necesita leer nada. Eso quedó implementado y sigue estando
(`PULSERIVAL_DEPOSITO=github`).

**Tercera y actual: un servidor con disco.** El panel de revisión cambió la
premisa. El panel lee el borrador, lo edita, lo aprueba o lo descarta: es
lectura y escritura en vivo sobre **la misma base que escribe el cron**. Con la
base dentro de la corrida de GitHub Actions y versionada en la rama `datos`, lo
que usted apruebe en el panel lo pisaría la corrida del lunes siguiente. Dos
bases divergiendo en silencio.

Y si ya hay un servidor con disco, ese mismo servidor sirve la landing y corre
el cron. Una pieza, no tres.

| | Servidor con disco | Vercel Pro |
|---|---|---|
| Costo | ~$5/mes | $20/mes |
| Landing | sí | sí |
| Formulario de alta | sí, directo a la base | sí, depositando YAML |
| **Panel de revisión** | **sí** | **no: necesita disco** |
| Cron | sí, del propio hosting | no |
| Base de datos | una, en disco | ninguna |

**Recomendación: un servidor con disco.** Railway o Fly.io, alrededor de $5 al
mes. Vercel queda como la opción si algún día se quiere la landing separada,
pero hoy no aporta nada y cuesta cuatro veces más.

## Publicar

### 1. Crear el servicio

Railway o Fly.io, apuntando a este repositorio. El `Procfile` ya está:

```
web: gunicorn 'pulserival.web.app:wsgi()' --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

**`--workers 1` es a propósito.** SQLite aguanta muchos lectores y un solo
escritor. Con este volumen un proceso sobra, y evita que dos escrituras se
peleen por el archivo. Cuando haga falta más, el paso siguiente es Postgres, no
más workers.

### 2. Un disco persistente

Montado donde apunte `PULSERIVAL_DB`. Sin disco, la base se crea vacía en cada
reinicio y se pierde todo: los clientes, los anuncios detectados —que son lo que
permite decir "esto es nuevo"— y los reportes.

### 3. Variables de entorno

```
PULSERIVAL_DB=/datos/pulserival.db     # dentro del disco persistente
PULSERIVAL_PANEL_CLAVE=...             # sin esto el panel no se sirve
PULSERIVAL_SECRET=...                  # firma la cookie de sesión del panel
PULSERIVAL_HTTPS=1                     # la cookie solo viaja por HTTPS
PULSERIVAL_COBRO=simulado              # 'real' cuando haya pasarela
PULSERIVAL_VERIFICAR_ALTA=0            # 1 para verificar al dar de alta (gasta)

APIFY_TOKEN=...                        # sin esto no hay datos reales
GEMINI_API_KEY=...                     # sin esto no hay análisis
GROQ_API_KEY=...                       # respaldo
RESEND_API_KEY=...                     # o SMTP_*, solo para enviar
```

Detalle que se paga caro si se olvida: **sin claves de IA el borrador lo escribe
el respaldo del código**, y el control de calidad lo rechaza con
`sin_interpretacion`. El pipeline no se cae, pero no hay análisis.

### 4. Traer la base que ya existe

Una sola vez, para no arrancar de cero y perder el historial de anuncios:

```bash
git fetch origin datos
git show datos:datos/pulserival.db > pulserival.db
# y subirlo al disco del servicio
```

Las migraciones son aditivas y corren solas al arrancar: una base vieja se
actualiza sin perder nada.

### 5. El cron en el servidor

Una tarea programada del hosting (Railway: "Cron Schedule") con el mismo comando
de siempre:

```
python -m pulserival.cli ciclo --modo auto --limite 50
```

No hay lógica nueva: es el mismo `pipeline.ciclo_completo()` que corre hoy en
Actions.

### 6. Apagar el cron de GitHub Actions

**Este paso no es opcional.** Con los dos corriendo, hay dos bases distintas y
lo que usted apruebe en el panel lo pisa la corrida de Actions.

No hace falta editar el workflow. En GitHub:
**Settings → Secrets and variables → Actions → Variables**, y crear:

```
PULSERIVAL_CRON_EN_SERVIDOR = 1
```

El job se saltea solo. El disparo a mano desde la pestaña Actions sigue
funcionando, para poder correr una prueba desde ahí. Volver atrás es cambiar el
`1` por un `0`.

### 7. Comprobar

```
GET /salud
→ {"ok": true, "deposito": "sqlite", "cobro": "simulado",
   "panel": "habilitado", "sesiones": "persistentes", "clientes": N}
```

`"panel": "sin PULSERIVAL_PANEL_CLAVE"` significa que el panel está apagado.
`"sesiones": "efímeras"` significa que falta `PULSERIVAL_SECRET` y que va a
tener que entrar de nuevo en cada reinicio.

Después: entre a `/panel/`, abra un reporte, y previsualice el correo antes de
aprobar nada.

## Probarlo local antes de publicar

```bash
PULSERIVAL_PANEL_CLAVE=probando python3 -m pulserival.cli servidor
```

Levanta la landing en `http://127.0.0.1:5000` y el panel en
`http://127.0.0.1:5000/panel/`. Avisa si el panel quedó apagado y si el cobro
está simulado. Para tener reportes que revisar sin gastar nada:

```bash
python3 -m pulserival.cli ciclo --modo demo
```

## La otra opción: la landing en Vercel

Sigue implementada y documentada por si algún día hace falta. Con
`PULSERIVAL_DEPOSITO=github` el alta se deposita como YAML en el repositorio y
el cron la carga con `aplicar-config`. Lo que **no** funciona ahí es el panel:
las funciones de Vercel no tienen disco persistente en ningún plan, ni en Pro.

Los archivos están: `wsgi.py`, `vercel.json`, `.vercelignore`. Y una advertencia
que conviene recordar: el plan Hobby prohíbe el uso comercial, y la definición de
Vercel incluye "publicitar la venta de un producto o servicio", así que una
landing que ofrece una suscripción necesita Pro **aunque no se cobre nada
todavía**.

## Qué NO está hecho

- **El webhook de la pasarela.** Hoy el cobro es simulado. Con
  `PULSERIVAL_COBRO=real` el checkout manda al enlace de pago y la confirmación
  por navegador queda bloqueada (403), pero todavía no existe el endpoint que
  recibe la notificación y activa solo. Hasta que exista se activa a mano:
  `cli clientes activar --id N --referencia "<comprobante>"`.
  Ver `docs/08-cobro-y-lanzamiento.md` para qué falta probar con una transacción
  real.
- **El panel no administra clientes ni dispara el ciclo.** Eso sigue por CLI, a
  propósito: un botón que gasta plata de Apify es un botón que se aprieta sin
  pensar.
- **No hay usuarios en el panel, hay una contraseña.** Con una persona operando
  alcanza. Ver `docs/10-panel.md`.
