# 11 · Publicar el core en un servidor

## Railway o Fly.io

Verificado el 2026-09-27; estos precios cambian seguido, conviene confirmarlos.

| | Railway (Hobby) | Fly.io |
|---|---|---|
| Cuota fija | $5/mes, con $5 de uso incluido | ninguna |
| Máquina | dentro de los $5 | $1,94/mes (shared-cpu-1x, 256 MB) |
| Disco | **5 GB incluidos** | $0,15 por GB al mes |
| Respaldos del disco | **manuales y automáticos, integrados** | a mano (`sqlite3 .backup`) |
| Deploy | con `git push`, sin instalar nada | `fly deploy` con flyctl |
| Postgres, cuando SQLite no alcance | un clic, en el mismo proyecto | hay que operarlo |
| Costo hoy | $5/mes | ~$2,10/mes |

**Fly es más barato. Railway es más fácil de operar.** Con un solo fundador no
técnico manteniendo esto, los $3 de diferencia pesan menos que:

1. **Los respaldos automáticos del disco.** En Fly el respaldo de la base es un
   comando que alguien tiene que acordarse de correr. Una base corrupta sin
   respaldo se lleva el historial de anuncios, que es lo que permite decir
   "esto es nuevo".
2. **El camino a Postgres.** El día que SQLite no alcance —más clientes, el
   panel y el ciclo escribiendo a la vez— el paso siguiente es Postgres. En
   Railway es un clic dentro del mismo proyecto.
3. **Deploy con `git push`**, sin instalar ni aprender una herramienta nueva.

**Ninguno de los dos sirve gratis.** Railway: $5 una vez, hasta 30 días, y
después $1/mes de crédito con disco máximo de 0,5 GB. Fly: 2 horas de máquina
o 7 días. Probar un ciclo semanal necesita semanas reales de calendario.

Y un detalle que solo aplica mientras no se pague: **Railway borra los discos
de las cuentas de prueba** 30 días después de que se acaban los créditos.
Pagando el plan Hobby eso no aplica.

## Lo que ya está en el repositorio

- `Dockerfile` — sirve para los dos. El puerto sale de `$PORT` si está
  (Railway lo inyecta) y si no del 8080 (Fly lo toma del `fly.toml`).
- `railway.json` — fuerza el Dockerfile, una réplica, chequeo en `/salud`.
- `fly.toml` — una máquina, disco en `/datos`, variables del cron.
- `.dockerignore` — la base y los borradores no entran a la imagen.

**No se construyó la imagen acá** (este contenedor no tiene demonio de Docker).
Lo que sí está probado son los dos casos del arranque: con `PORT` inyectado y
sin él, gunicorn levanta y `/salud` responde. Hay tests que dejan clavado que
el `CMD` use `$PORT` y forma shell: con el puerto fijo, Railway responde
"Application failed to respond" aunque el contenedor parezca sano.

## Un solo servicio, y por qué

Los dos tienen la misma restricción: **un disco se monta en un solo sitio.**
Fly lo dice así: "there's a one-to-one mapping between Machines and volumes".
Railway así: "Each service can only have a single volume".

El panel escribe en la base (guarda su versión, marca enviado) y el ciclo
también. Dos servicios serían dos bases distintas divergiendo en silencio. Por
eso el cron vive **dentro del proceso web**
(`pulserival/web/planificador.py`) y por eso hay un solo worker y una sola
réplica: dos serían dos planificadores compitiendo por la misma base.

Y por eso el servicio **no puede dormirse**. Una instancia dormida despierta
con una petición, no con un horario: con el ahorro de apagarla no habría
reportes. En Railway eso significa **no activar "Serverless"**; en Fly,
`auto_stop_machines = false`.

## Instalar en Railway

### 1. Crear el proyecto

En [railway.com](https://railway.com): **New Project → Deploy from GitHub repo**,
elegir este repositorio y la rama. Railway detecta el `Dockerfile` y lo usa.

Si el primer deploy falla diciendo que no sabe cómo construir la app, es un
problema conocido de Railway con la autodetección. `railway.json` ya fuerza el
constructor; si igual pasa, en **Settings → Build** hay que elegir Dockerfile a
mano.

**Si los logs repiten `'$PORT' is not a valid port number`**, Railway está
usando un comando de arranque que no expande la variable. Revisar
**Settings → Deploy → Custom Start Command**: tiene que estar **vacío**, para
que use el `CMD` del Dockerfile. (Este error lo causaba un `Procfile` que ya se
borró del repositorio; si Railway se lo guardó como comando personalizado en el
primer deploy, hay que limpiarlo a mano.)

### 2. El disco

**Settings → Volumes → New Volume**, montado en `/datos`. El plan Hobby trae
5 GB incluidos; con 1 sobra, la base pesa megabytes.

Sin disco, la base se borra en cada deploy y se pierde el historial de
anuncios, que es lo único que permite decir "esto es nuevo".

### 3. Las variables

En **Variables**. Railway no lee el `fly.toml`, así que las que allá están en
el archivo acá hay que ponerlas a mano:

```
PULSERIVAL_DB=/datos/pulserival.db
PULSERIVAL_BORRADORES=/datos/borradores
PULSERIVAL_SALIDA=/datos/salida
PULSERIVAL_PLANIFICADOR=1
PULSERIVAL_CICLO_CADA_DIAS=7
PULSERIVAL_CICLO_HORA_UTC=11
PULSERIVAL_CICLO_MODO=auto
PULSERIVAL_CICLO_LIMITE=40
PULSERIVAL_HTTPS=1
PULSERIVAL_COBRO=simulado
PULSERIVAL_VERIFICAR_ALTA=0
```

Y los secretos:

```
PULSERIVAL_PANEL_CLAVE=una-contraseña-larga-y-suya
PULSERIVAL_SECRET=<32 caracteres al azar>
APIFY_TOKEN=...
GEMINI_API_KEY=...
GROQ_API_KEY=...
```

**`PULSERIVAL_SECRET` hay que generarlo.** Son 32 caracteres al azar; da
igual cómo se generen mientras no los elija una persona. En la **terminal**
(no adentro de Python), cualquiera de estos:

```bash
openssl rand -base64 32                                    # macOS y Linux, sin instalar nada
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # si tiene Python
```

En Windows, en PowerShell:

```powershell
-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 43 | % {[char]$_})
```

Railway tiene una función `${{secret()}}`, pero **solo funciona al crear
plantillas**, no en la pestaña Variables de un servicio ya hecho.

Esta variable firma la cookie de sesión del panel y nada más: no protege datos
de clientes. Si falta, el servidor genera una al azar en cada arranque y usted
tiene que volver a entrar al panel después de cada deploy (`/salud` lo dice:
`"sesiones": "efímeras"`). Cambiarla después es gratis: solo cierra las
sesiones abiertas.

**`PULSERIVAL_PANEL_CLAVE` la elige usted.** Es la que va a escribir para
entrar al panel. Larga y que no use en ningún otro lado: con ella se pueden
mandar correos a sus clientes.

`RESEND_API_KEY` solo cuando quiera enviar de verdad. Sin ella el panel igual
deja simular el envío y guarda la copia.

Sin `APIFY_TOKEN` no hay datos reales. Sin clave de IA el borrador lo escribe
el respaldo del código y el control de calidad lo rechaza con
`sin_interpretacion` — eso no es un error, es el control funcionando.

### 4. NO activar "Serverless"

Railway puede dormir el servicio cuando no recibe tráfico. **Con eso encendido
no hay reportes**: el planificador vive dentro del proceso y una instancia
dormida despierta con una petición, no con un horario.

### 5. El dominio

**Settings → Networking → Generate Domain**. Railway detecta el puerto del
contenedor solo, porque el `Dockerfile` escucha en `$PORT`.

Comprobar:

```
GET https://<su-app>.up.railway.app/salud
```

```json
{"ok": true, "panel": "habilitado", "sesiones": "persistentes",
 "planificador": "encendido", "cadencia_dias": 7.0, "hora_utc": 11,
 "disco": "/datos: disco montado, sobrevive a los deploys",
 "siguiente": "no hay ninguna corrida todavía: la primera se dispara a mano"}
```

Qué mirar, en orden de gravedad:

- **`"disco"` empezando con `ATENCIÓN`** es lo más grave y lo más silencioso.
  Quiere decir que el volumen no está montado en la ruta de `PULSERIVAL_DB`.
  SQLite escribe igual —en el sistema de archivos del contenedor— y todo
  funciona perfecto **hasta el deploy siguiente**, que se lleva los clientes y
  el historial de anuncios. Ese historial no se recupera: las plataformas solo
  muestran lo que está activo hoy. Arreglarlo es montar el volumen exactamente
  donde apunta `PULSERIVAL_DB`.
- `"sesiones": "efímeras"`: falta `PULSERIVAL_SECRET` y va a tener que entrar
  al panel de nuevo en cada reinicio.
- `"panel": "sin PULSERIVAL_PANEL_CLAVE"`: el panel está apagado.

**Ojo**: `/salud` devuelve 500 cuando falta configurar algo, y el chequeo de
salud de Railway marca el deploy como fallido. Es a propósito: mejor que quede
a la vista que descubrirlo por un alta perdida.

### 5.5 Preparar `railway ssh` (una sola vez)

Todo lo que sigue se corre **desde la terminal de su máquina**; `railway ssh --`
es lo que hace que el comando se ejecute adentro del contenedor.

Primero, el CLI:

```bash
brew install railway                          # Mac con Homebrew
bash <(curl -fsSL railway.com/install.sh)     # Mac o Linux sin Homebrew
npm i -g @railway/cli                         # Windows, o cualquiera con Node
```

```bash
railway login
railway link          # elegir proyecto, entorno y servicio
```

`railway ssh` necesita una llave SSH registrada, y si no la hay falla con
"No SSH keys found in your SSH agent or ~/.ssh/":

```bash
ssh-keygen -t ed25519      # Enter tres veces: ruta por defecto, sin contraseña
railway ssh keys add       # elegir id_ed25519.pub de la lista
```

Si después dice **"No registered SSH keys found"** teniendo la llave puesta, es
un problema conocido del CLI: la registra como llave personal y el servicio
puede estar pidiendo una de workspace.

```bash
railway ssh keys remove
railway ssh keys add --workspace
```

Comprobar que llega antes de tocar nada:

```bash
railway ssh -- ls -la /datos
```

Tiene que listar el contenido del disco. Si esto falla, nada de lo que sigue
va a funcionar.

### 6. Traer la base que ya existe

Una sola vez, para no perder el historial:

```bash
git fetch origin datos
git show datos:datos/pulserival.db > /tmp/pulserival.db
railway link                                    # elegir proyecto y servicio
railway ssh -- sh -c "cat > /datos/pulserival.db" < /tmp/pulserival.db
railway ssh -- ls -la /datos                    # comprobar que llegó y pesa algo
railway redeploy                                # que el proceso la lea de nuevo
```

**`railway ssh`, no `railway run`.** `railway run` ejecuta el comando **en su
máquina** con las variables del servicio inyectadas: escribiría en un `/datos`
de su computadora y la base del servidor quedaría intacta, sin que nada avise.
`railway ssh` es el que entra al contenedor.

Si arranca sin esto no pasa nada malo: el esquema se crea solo y las
migraciones son aditivas.

### 7. Cargar los clientes y la primera corrida

```bash
railway ssh -- python -m pulserival.cli aplicar-config
railway ssh -- python -m pulserival.cli clientes lista
railway ssh -- python -m pulserival.cli prueba-scraper --consulta "Tienda Monge"
railway ssh -- python -m pulserival.cli ciclo --modo auto --limite 40
```

El `prueba-scraper` antes del ciclo cuesta centavos y confirma que
`APIFY_TOKEN` funciona de verdad, antes de disparar la corrida completa.

**El planificador no dispara la primera corrida solo.** Es la única forma de
que un deploy no pueda gastar plata por su cuenta, y además es el orden
correcto: comprobar que el scraper y las claves andan antes de dejarlo en
automático. Desde esa corrida toma el control: cada 7 días, a partir de las
11:00 UTC (5:00 a.m. en Costa Rica).

### 8. Los respaldos

**Settings → Volumes → Backups.** Es la razón principal para elegir Railway:
active los automáticos ahora, no cuando haga falta.

### 9. Apagar el cron de GitHub Actions

**No es opcional.** Con los dos corriendo hay dos bases y lo que usted apruebe
en el panel lo pisa la corrida de Actions.

En GitHub: **Settings → Secrets and variables → Actions → Variables**, crear
`PULSERIVAL_CRON_EN_SERVIDOR = 1`. El disparo a mano sigue funcionando.

## Operar en Railway

```bash
railway logs                                             # incluye el planificador
railway ssh -- python -m pulserival.cli costos
railway ssh -- python -m pulserival.cli clientes lista
railway ssh -- python -m pulserival.cli reporte lista
railway ssh -- python -m pulserival.cli ciclo --modo auto   # forzar una corrida
```

Siempre `railway ssh --`, nunca `railway run`: el segundo corre en su máquina
con las variables del servicio inyectadas, así que apuntaría a un `/datos` que
en su computadora no es la base del servidor. El comando no falla, y esa es la
parte peligrosa.

El panel está en `https://<su-app>.up.railway.app/panel/` y el gasto en
`/panel/gasto`.

## Instalar en Fly.io

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

## Operar en Fly

```bash
fly logs                                    # incluye lo que hace el planificador
fly ssh console -C "python -m pulserival.cli costos"
fly ssh console -C "python -m pulserival.cli clientes lista"
fly ssh console -C "python -m pulserival.cli ciclo --modo auto"   # forzar
fly ssh console -C "sqlite3 /datos/pulserival.db .backup /datos/respaldo.db"
```

El panel está en `https://<su-app>.fly.dev/panel/` y la vista de gasto en
`/panel/gasto`.

## Qué puede salir mal, en cualquiera de los dos

- **Se queda sin memoria.** Un ciclo con muchos anuncios aprieta. Si aparece
  OOM en los logs, subir la memoria: en Railway se ajusta solo dentro del
  plan; en Fly, `fly scale memory 512`, unos $2 más al mes.
- **La base se corrompe.** SQLite sobre un disco de red es más frágil que
  sobre uno local. En Railway, los respaldos automáticos del volumen cubren
  esto; en Fly hay que correrlos a mano.
- **El servicio se duerme.** Si alguien activa "Serverless" en Railway o
  `auto_stop_machines` en Fly, el panel sigue andando —despierta con la
  visita— pero **los reportes dejan de generarse en silencio**. `/salud`
  muestra cuándo fue la última corrida: si pasaron más días que la cadencia,
  es esto.
- **El deploy ignora el Dockerfile.** Le pasa a Railway con la autodetección.
  Se nota porque el arranque no respeta `--workers 1`. Elegir Dockerfile a
  mano en Settings → Build.
