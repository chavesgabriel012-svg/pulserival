# 09 · Dónde y cómo se publica

## La pregunta corta: ¿se puede en Vercel?

**Sí, y es lo que quedó implementado.** Hay una sola restricción real, y no se
arregla pagando el plan Pro: **en Vercel no se puede guardar en SQLite.** La
solución no fue cambiar de hosting ni cambiar la base, fue sacar la base del
camino del formulario.

Corrijo lo que decía este archivo antes ("la landing sí, el backend no"): era
una conclusión correcta sobre SQLite y una conclusión equivocada sobre Vercel.
El servidor Flask completo corre en Vercel sin cambios de framework; Flask es
uno de los backends que Vercel detecta solo.

### Las dos objeciones, separadas

**1. El plan.** El plan Hobby está limitado a uso personal no comercial. La
definición de Vercel incluye explícitamente "publicitar la venta de un producto
o servicio", no solamente cobrar. Una landing que ofrece una suscripción de
$20/mes entra ahí **aunque todavía no se procese ningún pago**. Así que el plan
Pro ($20/mes) hace falta desde el primer día público, no desde el primer cobro.
Con Pro, esta objeción desaparece del todo.

**2. La base de datos.** Esta no depende del plan. Las funciones de Vercel no
tienen disco persistente en ningún plan: el único directorio escribible es
`/tmp`, se borra entre invocaciones y cada instancia tiene el suyo. Un alta
escrita en una base SQLite ahí se guardaría y desaparecería, y peor: la petición
siguiente podría leer una base distinta. Las opciones de almacenamiento de
Vercel son en red (Blob, Postgres, Redis), no un disco.

### Cómo queda resuelta la segunda

El servidor web **no necesita la base para nada de lo que hace hoy**. En el
modelo híbrido que elegimos, el alta no se activa sola: la activa una persona
después de confirmar el pago. Entonces el trabajo del servidor es uno solo:
**guardar la solicitud en un lugar que no se borre, y avisar**.

Ese lugar ya existe y ya es la fuente de verdad de quiénes son los clientes:
`config/clientes.yaml`. El archivo existe precisamente porque la base no se
versiona. Así que el alta se deposita como un YAML nuevo en el repositorio,
por la API de GitHub:

```
formulario en Vercel
    │
    ├─ valida (el mismo código que el CLI: pulserival/altas.py)
    │
    └─ escribe config/altas/2026-09-26-ferreteria-el-tornillo.yaml
           (activo: false · estado_suscripcion: pendiente_pago)
                │
                │   usted revisa, confirma el pago, pone activo: true
                ▼
       el cron de GitHub Actions que ya corre los lunes
           → aplicar-config lee clientes.yaml + config/altas/*.yaml
           → ciclo_completo genera el reporte
```

No hay base nueva, no hay servicio nuevo, no hay librería nueva. El paso
`aplicar-config` del workflow no cambió una línea: ya corría antes del ciclo.

### Lo que esto cuesta

| | Con esta solución | Servidor con disco (Railway/Fly) |
|---|---|---|
| Hosting | Vercel Pro, $20/mes | ~$5/mes |
| Base de datos | la del cron, sin cambios | una sola, en el servidor |
| El alta aparece en la base | en la corrida siguiente | al instante |
| Activación | manual (la decidimos así) | manual igual, por ahora |
| Webhook de pasarela | funciona igual | funciona igual |
| Piezas que mantener | una | una |

Lo único que se pierde es la inmediatez: el cliente no queda en la base al
segundo. En el modelo híbrido eso no cambia nada, porque de todas formas hay una
revisión humana antes de la primera corrida.

Lo que **no** se pierde: el webhook de la pasarela, cuando exista, también
puede correr en Vercel (recibe, verifica la firma, deposita la confirmación).
Nada de lo que falta obliga a mudarse.

Si algún día el volumen hace que la revisión manual estorbe, el cambio es mover
la base a un servidor con disco y poner `PULSERIVAL_DEPOSITO=sqlite`. El código
del formulario no se toca: es la misma aplicación Flask.

## Publicar en Vercel

### 1. Los archivos ya están en el repositorio

- `wsgi.py` — Vercel busca una instancia de Flask llamada `app` en `wsgi.py`
  (entre otros nombres) y con eso despliega todo el servidor como una sola
  función, sin configuración. El archivo solo llama a `crear_app()`.
- `vercel.json` — `maxDuration: 30`.
- `.vercelignore` — la base, los borradores y los tests no van al paquete.
- `requirements.txt` — Flask y PyYAML ya estaban.

### 2. Un token de GitHub

Hace falta uno con permiso de **escritura de contenido** sobre este
repositorio, y nada más. Un fine-grained token con `Contents: Read and write`
alcanza. Va en las variables de entorno de Vercel, nunca en el repositorio.

### 3. Variables de entorno en Vercel

```
PULSERIVAL_GITHUB_TOKEN=...            # el del punto 2
PULSERIVAL_REPO=usuario/pulserival     # dónde deposita las altas
PULSERIVAL_RAMA=main                   # opcional, main por defecto
PULSERIVAL_COBRO=simulado              # 'real' cuando haya pasarela
```

`PULSERIVAL_DEPOSITO` no hace falta: en Vercel el valor por defecto ya es
`github`, porque `sqlite` ahí no guardaría nada. Si alguien lo pone en `sqlite`
igual, `/salud` responde 500 y dice por qué.

`APIFY_TOKEN` y las claves de IA **no van en Vercel**: el servidor web no llama
a ningún scraper ni a ninguna IA. Siguen siendo secretos del repositorio, que es
donde corre el ciclo.

### 4. Comprobar que quedó bien

```
GET /salud
→ {"ok": true, "deposito": "github", "cobro": "simulado"}
```

Si responde `{"ok": false, "falta_configurar": [...]}`, dice exactamente qué
variable falta. Conviene mirarlo **antes** de mandar la dirección a alguien: un
formulario publicado que no puede guardar pierde altas en silencio.

### 5. Una alta de prueba

Llene el formulario con datos suyos. Tiene que aparecer un commit nuevo en
`config/altas/`. Después:

```bash
git pull
python3 -m pulserival.cli altas
```

## La otra opción, para tenerla escrita

Un servidor con disco (Railway, Fly.io, ~$5/mes) con `PULSERIVAL_DEPOSITO=sqlite`:
el alta entra a la base al instante y el checkout simulado funciona completo
(activa la suscripción, que sin base no hay nada que activar). El `Procfile` ya
está:

```
web: gunicorn 'pulserival.web.app:wsgi()' --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

**`--workers 1` es a propósito.** SQLite aguanta muchos lectores y un solo
escritor. Con este volumen un proceso sobra, y evita que dos escrituras se
peleen por el archivo. Cuando haga falta más, el paso siguiente es Postgres, no
más workers.

Si se elige este camino, la base del servidor y la de la rama `datos` **no
pueden convivir**: serían dos bases divergiendo en silencio, y el cliente que se
dio de alta por la web no existiría para el cron. Habría que copiar la base a
disco una vez y apagar el `schedule:` de `.github/workflows/recoleccion.yml`.

Con la solución de Vercel ese problema no existe: hay una sola base, la del
cron, y el servidor web no la toca.

## Probarlo local antes de publicar

```bash
python3 -m pulserival.cli servidor
```

Levanta en `http://127.0.0.1:5000` con depósito `sqlite` y cobro simulado, así
se recorre el flujo completo incluido el checkout. Para probar el camino de
Vercel sin publicar nada:

```bash
PULSERIVAL_DEPOSITO=github PULSERIVAL_REPO=usuario/pulserival \
  PULSERIVAL_GITHUB_TOKEN=... python3 -m pulserival.cli servidor
```

Ojo: eso **escribe de verdad** en el repositorio.

## Qué NO está hecho todavía

- **El webhook de la pasarela.** Hoy el cobro es simulado. Con
  `PULSERIVAL_COBRO=real` el checkout manda al enlace de pago y la confirmación
  por navegador queda bloqueada (403), pero todavía no existe el endpoint que
  recibe la notificación y activa solo. Hasta que exista se activa a mano.
  Ver `docs/08-cobro-y-lanzamiento.md` para qué falta probar con una
  transacción real.
- **El panel de administración web.** Por ahora `cli altas` lista la bandeja y
  `cli clientes lista` la base.
- **El límite por IP del formulario no sirve en Vercel.** Está en memoria del
  proceso, y en serverless cada instancia tiene la suya: cinco peticiones por
  minuto por instancia, no en total. No agrega riesgo de gasto (el servidor no
  llama al scraper: `PULSERIVAL_VERIFICAR_ALTA` queda apagado), pero alguien
  podría dejar muchos archivos basura en `config/altas/`. Si pasa, la respuesta
  es una regla de rate limiting en el firewall de Vercel, no código nuevo acá.
