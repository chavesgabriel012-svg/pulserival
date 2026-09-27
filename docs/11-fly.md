# 11 · Publicar en Fly.io

## Por qué Fly y no Railway

Verificado el 2026-09-27; estos precios cambian seguido, conviene confirmarlos.

| | Fly.io | Railway |
|---|---|---|
| Prueba gratis | 2 horas de máquina **o** 7 días | $5 una vez, hasta 30 días |
| Plan gratuito permanente | no existe desde octubre 2024 | $1/mes de crédito, disco máx. 0,5 GB |
| Cuota fija mensual | **ninguna** | $5/mes en Hobby, se usen o no |
| Máquina | $1,94/mes (shared-cpu-1x, 256 MB) | incluida en los $5 |
| Disco | $0,15 por GB al mes | ~$0,17 por GB al mes |
| **Costo de PulseRival** | **~$2,10/mes** | **$5/mes mínimo** |

Dos cosas decidieron:

1. **Fly no tiene piso.** Railway cobra $5 aunque el servicio esté apagado.
2. **Railway borra los discos de las cuentas de prueba** 30 días después de que
   se acaban los créditos. El historial de anuncios es justamente lo que
   permite decir "esto es nuevo"; perderlo vacía el producto.

**Ninguno de los dos sirve gratis para esto.** Las 2 horas de Fly no alcanzan
para dejar nada corriendo, y probar un ciclo semanal necesita semanas reales.

## Lo que ya está en el repositorio

- `Dockerfile` — imagen chica, gunicorn con `--workers 1`.
- `fly.toml` — una máquina, disco montado en `/datos`, variables del cron.
- `.dockerignore` — la base y los borradores no entran a la imagen.

**No se construyó la imagen acá** (este contenedor no tiene demonio de Docker).
Lo que sí está probado es el comando exacto que corre el Dockerfile: gunicorn
levanta, `/salud` responde, la landing y el panel cargan.

## Una sola máquina, y por qué

En Fly **un volumen se monta en una sola máquina**: "there's a one-to-one
mapping between Machines and volumes". El panel escribe en la base (guarda su
versión, marca enviado) y el ciclo también. Dos máquinas serían dos bases
distintas divergiendo en silencio.

Por eso el cron vive **dentro del proceso web** (`pulserival/web/planificador.py`)
y por eso `auto_stop_machines = false`: una máquina dormida despierta con una
petición, no con un horario. Apagarla ahorraría alrededor de $1,80 al mes y a
cambio no habría reportes.

## Instalar

### 1. Crear la app y el disco

```bash
fly auth signup            # o: fly auth login
fly launch --no-deploy     # usa el fly.toml que ya está; no lo deje regenerar
fly volumes create pulserival_datos --region mia --size 1
```

1 GB sobra: la base pesa megabytes. Son $0,15 al mes.

### 2. Los secretos

Nunca en `fly.toml`, que va al repositorio:

```bash
fly secrets set \
  PULSERIVAL_PANEL_CLAVE="una-contraseña-larga-y-suya" \
  PULSERIVAL_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  APIFY_TOKEN="..." \
  GEMINI_API_KEY="..." \
  GROQ_API_KEY="..."
```

`RESEND_API_KEY` solo cuando quiera enviar de verdad. Sin ella, el panel
igual deja simular el envío y guarda la copia.

Sin `APIFY_TOKEN` no hay datos reales. Sin clave de IA el borrador lo escribe
el respaldo del código y el control de calidad lo rechaza con
`sin_interpretacion` — eso no es un error, es el control funcionando.

### 3. Desplegar

```bash
fly deploy
fly status
curl https://<su-app>.fly.dev/salud
```

`/salud` tiene que responder algo así:

```json
{"ok": true, "panel": "habilitado", "sesiones": "persistentes",
 "planificador": "encendido", "cadencia_dias": 7.0, "hora_utc": 11,
 "siguiente": "no hay ninguna corrida todavía: la primera se dispara a mano"}
```

Si dice `"sesiones": "efímeras"` falta `PULSERIVAL_SECRET` y va a tener que
entrar al panel de nuevo en cada reinicio.

**Ojo con el chequeo de salud**: `/salud` devuelve 500 cuando falta
configurar algo, y Fly reinicia la máquina cuando eso pasa. Un deploy sin los
secretos puestos se reinicia en bucle. Es a propósito: mejor que quede a la
vista.

### 4. Traer la base que ya existe

Una sola vez, para no perder el historial de anuncios:

```bash
git fetch origin datos
git show datos:datos/pulserival.db > /tmp/pulserival.db
fly ssh sftp shell
  put /tmp/pulserival.db /datos/pulserival.db
fly apps restart pulserival
```

Si arranca sin esto no pasa nada malo: el esquema se crea solo al arrancar y
las migraciones son aditivas.

### 5. Cargar los clientes

```bash
fly ssh console -C "python -m pulserival.cli aplicar-config"
```

### 6. La primera corrida, a mano

**El planificador no dispara la primera corrida solo.** Es a propósito: es la
única forma de que un deploy no pueda gastar plata por su cuenta, y además es
el orden correcto — comprobar que el scraper y las claves andan antes de dejarlo
en automático.

```bash
fly ssh console -C "python -m pulserival.cli prueba-scraper --consulta 'Tienda Monge'"
fly ssh console -C "python -m pulserival.cli ciclo --modo auto --limite 40"
```

Desde esa corrida el planificador toma el control: cada 7 días, a partir de las
11:00 UTC (5:00 a.m. en Costa Rica).

### 7. Apagar el cron de GitHub Actions

**No es opcional.** Con los dos corriendo hay dos bases y lo que usted apruebe
en el panel lo pisa la corrida de Actions.

En GitHub: **Settings → Secrets and variables → Actions → Variables**, crear
`PULSERIVAL_CRON_EN_SERVIDOR = 1`. El disparo a mano sigue funcionando.

## Cómo decide el planificador

No es un cron y la diferencia importa. Un cron dispara a una hora fija y lo que
se perdió se perdió: si la máquina estaba reiniciándose el lunes a las 5, esa
semana no hay reporte y nadie se entera hasta que el cliente pregunta.

Cada hora el hilo mira **cuándo fue la última recolección**:

- ¿Hace menos de 7 días? No hace nada. Es el caso normal y no cuesta nada:
  lee una fila de la base.
- ¿Ya pasaron 7 días? Corre, esperando a las 11:00 UTC. Si el atraso pasa de un
  día, corre sin esperar.
- ¿Hay una corrida `en_curso` de hace menos de 90 minutos? No arranca otra.
- ¿No hay ninguna corrida? No arranca sola (paso 6).

**El tic nunca llama al scraper para averiguar si hay trabajo.**
`ciclo_completo()` recolecta primero y recién después mira a quién le toca, así
que llamarlo cada hora pagaría Apify veinticuatro veces por día. Hay un test que
lo deja clavado.

Variables, todas en `fly.toml`:

```
PULSERIVAL_PLANIFICADOR=1          # 0 lo apaga
PULSERIVAL_CICLO_CADA_DIAS=7
PULSERIVAL_CICLO_HORA_UTC=11       # 5:00 a.m. Costa Rica
PULSERIVAL_CICLO_MODO=auto         # demo para probar sin gastar
PULSERIVAL_CICLO_LIMITE=40         # anuncios por competidor
```

Para probar el ciclo completo sin gastar un centavo, `PULSERIVAL_CICLO_MODO=demo`
usa datos de ejemplo y no llama a ningún scraper.

## Operar

```bash
fly logs                                    # incluye lo que hace el planificador
fly ssh console -C "python -m pulserival.cli costos"
fly ssh console -C "python -m pulserival.cli clientes lista"
fly ssh console -C "python -m pulserival.cli ciclo --modo auto"   # forzar
fly ssh console -C "sqlite3 /datos/pulserival.db .backup /datos/respaldo.db"
```

El panel está en `https://<su-app>.fly.dev/panel/` y la vista de gasto en
`/panel/gasto`.

## Qué puede salir mal

- **La máquina se queda sin memoria.** 256 MB alcanzan para gunicorn con un
  worker, pero un ciclo con muchos anuncios puede apretar. Si aparece OOM en
  `fly logs`, subir a 512 MB (`fly scale memory 512`) son unos $2 más al mes.
- **La base se corrompe.** SQLite en un disco de red es más frágil que en uno
  local. Hacer el respaldo de arriba antes de cada deploy grande.
- **El disco se llena.** 1 GB con miniaturas y copias de correos tarda años,
  pero `fly volumes list` lo muestra.
