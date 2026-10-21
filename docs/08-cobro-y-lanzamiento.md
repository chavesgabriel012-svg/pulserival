# 08 · Cobro y lanzamiento (Paddle)

Este documento es la lista de lo que falta antes de cobrarle a una persona
real, y de lo que **no** se puede dar por probado porque todavía no pasó
plata de verdad por el sistema.

**Estado: el cobro con Paddle está construido y probado solo contra eventos
de prueba firmados por los tests. Ninguna transacción, ni de sandbox ni
real, pasó todavía por el sistema.** El código arranca en modo `simulado` y,
aunque se active Paddle, usa el entorno `sandbox` salvo que se pida
`production` de forma explícita. No active `production` sin haber completado
la lista de la sección 6.

## 1. Qué se lanza

Dos planes, nada más:

| | Plan | Cómo se compra | Precio |
|---|---|---|---|
| 1 | **Reporte mensual** (`autoservicio`) | landing + checkout de Paddle | $25/mes (o $240 si paga el año) |
| 2 | **Custom** (`custom`), para agencias | conversación + link de pago hecho a mano en Paddle | cotizado: cada 2 días desde $300/mes, diario desde $500/mes |

La landing muestra dos tarjetas: el mensual de $25 y el Custom. **El anual no
es un plan, es una forma de pagar el mensual**, y aparece recién en el
checkout: "Todo el año, $240, Ahorra $60, le queda en $20 al mes". Cuesta $240
para que el cliente pague **una** comisión de Paddle al año en vez de doce.
El checkout deja marcado el mes a mes: elegir el año tiene que ser una
decisión, no algo que se pasó por alto.

La prueba gratis (`prueba`) y el plan semanal (`semanal`) siguen en
`config/planes.yaml` con `visible: false`. **Agregarlos después no requiere
migración**: el esquema ya acepta las periodicidades `semanal`, `mensual` y
`custom`, los ciclos `mensual` y `anual`, y cualquier nombre de plan. Para
publicar uno basta con ponerle `visible: true`, `checkout: true`, sus
`ciclos` y los ids de precio de Paddle en el YAML. Lo único que habría que
escribir es la regla de negocio que hoy no existe (por ejemplo, que la prueba
no pase por el checkout).

## 2. La tensión de arquitectura, y qué se eligió

El sistema nació con este modelo:

- un **cron de GitHub Actions** corre una vez por semana,
- trabaja sobre una base SQLite que vive **versionada en la rama `datos`**,
- y al terminar hace commit de la base.

Eso funciona porque **nadie más escribe en la base**: el cron es el único
escritor y corre de a uno.

Un webhook rompe las dos premisas. Paddle avisa de un pago **en cualquier
momento**, espera respuesta en **5 segundos** y, si no la recibe, reintenta
(en producción, hasta 60 veces en 3 días). Eso necesita algo **escuchando las
24 horas**, y ese algo **escribe en la base fuera del horario del cron**.

Las opciones que hay:

| | Opción | Problema |
|---|---|---|
| A | **Webhook en la app Flask que ya corre en Railway** (elegida) | Ninguno nuevo: el servidor ya está siempre encendido, ya tiene la base en un disco persistente y ya tiene el cron adentro. |
| B | Webhook en una función serverless (Vercel) que escriba en la rama `datos` | Dos escritores sobre un archivo binario en git: un pago que entra mientras corre el cron produce un commit en conflicto que git no puede fusionar. Además las funciones de Vercel no tienen disco. |
| C | Sin webhook: el cron le pregunta a la API de Paddle quién pagó | Un cliente que paga un lunes espera hasta una semana para que lo activen. Y sigue faltando la clave de la API. |
| D | Pasar a una base de datos alojada (Postgres) | Resuelve los dos escritores, pero es una migración de todo el sistema para un problema que la opción A ya resuelve. |

**Se eligió A.** El webhook es `POST /webhooks/paddle` en
`pulserival/web/app.py`, escribe en la misma SQLite del disco `/datos`, y el
cron que corre dentro del mismo proceso (`pulserival/web/planificador.py`) es
el único otro escritor. Dos escritores sobre el mismo archivo, en el mismo
servidor, SQLite los ordena solo; lo que no puede ordenar es dos copias de la
base en lugares distintos.

Un detalle de esa convivencia: si un pago llega justo mientras el ciclo tiene
la base tomada para escribir, el webhook espera hasta 5 segundos y, si no
alcanza, responde 500. Paddle toma el 500 como "no llegó" y reintenta más
tarde, así que el pago no se pierde, solo se activa con atraso. El evento
queda registrado en `eventos_pago` cuando por fin entra.

**La consecuencia que hay que respetar:** desde que Paddle está activo, **la
base de la verdad es la del disco de Railway, no la de la rama `datos`.** Si
el cron de GitHub Actions vuelve a correr, trabaja sobre la copia de git, que
no tiene los pagos, y esa copia diverge. Por eso, antes de activar Paddle:

- en GitHub, **Settings → Secrets and variables → Actions → Variables**,
  `PULSERIVAL_CRON_EN_SERVIDOR = 1` (apaga el cron de Actions; ver
  `docs/11-servidor.md`);
- y confirmar en `/salud` que el planificador del servidor está encendido.

Ninguna de estas dos cosas la puede verificar el código por usted.

### La migración del esquema

El cambio de esquema (periodicidad `custom`, ciclo de facturación, columnas
de Paddle y la tabla `eventos_pago`) **no se aplica en caliente sobre la base
de la rama `datos`**. Se corre una vez, a mano, con estos pasos:

```bash
# 1. Ver qué cambiaría, sobre una copia. No toca la base.
python -m pulserival.cli migrar --revisar --db ruta/a/pulserival.db

# 2. Aplicarlo.
python -m pulserival.cli migrar --db ruta/a/pulserival.db
```

Qué hace:

- Antes de tocar nada, guarda una copia completa en
  `respaldos/migraciones/antes-de-migrar-<fecha>.db`, al lado de la base.
  Esa carpeta queda fuera de la rotación de 7 respaldos, así que no se
  borra sola.
- Para cambiar el `CHECK` de `periodicidad`, SQLite obliga a reconstruir la
  tabla `clientes`. Se hace con las claves foráneas apagadas: con ellas
  prendidas, borrar la tabla vieja borraría en cascada todo el historial de
  anuncios, reportes y competidores. Al final verifica que la cantidad de
  clientes sea la misma y que no haya referencias rotas; si algo no cuadra,
  deshace todo.
- Un cliente que tenía `plan: custom`, o una cadencia distinta de la de su
  periodicidad, pasa a `periodicidad = 'custom'`.
- Es idempotente: correrla dos veces no hace nada la segunda.

Se probó sobre una copia de la base real de la rama `datos`: las 7 tablas
quedaron con la misma cantidad de filas y sin referencias rotas.

En Railway no hace falta correrla: el servidor la corre al arrancar, con el
mismo respaldo previo. Igual conviene bajar una copia de la base antes del
deploy (ver `docs/11-servidor.md`).

## 3. Configurar Paddle (primero en sandbox)

Todo esto se hace dos veces: una en
[sandbox-vendors.paddle.com](https://sandbox-vendors.paddle.com) y otra en
la cuenta real. Los ids de un entorno no sirven en el otro.

1. **Producto y precios.** Un producto "Reporte mensual" con dos precios
   recurrentes: $25 cada mes y $240 cada año. Copie los ids (`pri_...`) en
   `config/planes.yaml`:

   ```yaml
   autoservicio:
     paddle:
       sandbox:    { mensual: "pri_...", anual: "pri_..." }
       production: { mensual: "",        anual: ""        }
   ```

2. **Client-side token.** *Developer tools → Authentication*. En sandbox
   empieza con `test_`, en producción con `live_`. Va en
   `PADDLE_CLIENT_TOKEN`.

3. **Destino de notificaciones.** *Developer tools → Notifications → New
   destination*:
   - URL: `https://<su-dominio>/webhooks/paddle`
   - Eventos: `subscription.created`, `subscription.updated`,
     `subscription.activated`, `subscription.past_due`,
     `subscription.paused`, `subscription.resumed`,
     `subscription.canceled`, `transaction.completed`,
     `transaction.payment_failed`.
   - Copie el *secret key* del destino en `PADDLE_WEBHOOK_SECRET`.

4. **Link de pago por defecto.** *Checkout → Checkout settings → Default
   payment link*: `https://<su-dominio>/pagar`. Paddle lo exige para los
   links de pago que se arman a mano (los del plan Custom): cuando el
   cliente abre el link, Paddle lo manda a esa página con `?_ptxn=txn_...` y
   la página abre el checkout.

5. **Aprobación del dominio** (solo en producción). Paddle revisa el sitio
   antes de dejarlo cobrar, y pide que tenga **precios visibles, Términos y
   condiciones, Política de reembolso y Política de privacidad**, enlazadas
   desde la navegación. Las tres páginas existen (`/terminos`,
   `/reembolsos`, `/privacidad`, enlazadas desde el pie de todas las
   páginas), pero **les faltan sus datos legales**: ver la sección 7. Sin
   ellos Paddle no aprueba el dominio. El sandbox no pide aprobación.

### Variables del servidor

```
PULSERIVAL_COBRO=paddle            # 'simulado' mientras tanto
PADDLE_ENTORNO=sandbox             # 'production' solo después de la sección 6
PADDLE_CLIENT_TOKEN=test_...
PADDLE_WEBHOOK_SECRET=...          # el secret key del destino de notificaciones
PULSERIVAL_SECRET=...              # ya existe; ahora también firma los links del checkout
```

Con `PULSERIVAL_COBRO=paddle`, `/salud` responde 500 y lista lo que falte
(token, secreto, ids de precio, un token de un entorno usado en el otro)
hasta que todo esté. Es la forma de saber que la configuración está completa
sin probar un pago.

## 4. Cómo funciona el cobro, de punta a punta

**Plan mensual (autoservicio):**

1. La persona llena el formulario de `/aplicar` y marca la casilla de
   aceptación de los Términos y la Política de privacidad. Sin ella el
   servidor rechaza el alta: la Ley 8968 pide el consentimiento por escrito,
   y queda guardado con fecha y versión (`consentimiento_en`,
   `consentimiento_version`).
2. Se crea el cliente en `pendiente_pago` y se la manda a
   `/checkout/<id>?t=<firma>`. La firma impide que alguien cambie el número
   y vea o pague el checkout de otro cliente.
3. Ahí elige cómo pagar (mes a mes o el año) y la página abre el checkout
   de Paddle con el precio de esa opción, con el id del cliente (firmado) en
   `customData`. El webhook anota después en el cliente qué ciclo pagó.
4. Paddle cobra y avisa al webhook. El cliente pasa a `activa` y desde ese
   momento el ciclo lo incluye.
5. La persona vuelve a `/gracias/<id>`, que se recarga sola hasta que llegue
   la confirmación de Paddle.

**Qué hace cada evento:**

| Evento | Efecto |
|---|---|
| `transaction.completed` | Pago exitoso (alta o renovación): `pendiente_pago` o `vencida` → `activa`. |
| `subscription.*` | El estado de la suscripción manda: `active` → `activa`, `past_due` → `vencida`, `paused` → `pausada`, `canceled` → `cancelada`. |
| `transaction.payment_failed` | No cambia nada. En el checkout es una tarjeta rechazada que la persona reintenta; en una renovación Paddle avisa aparte con `subscription.past_due`. |

Solo `activa` recibe reportes y consume scraper. Una cancelación "al final
del periodo" no corta el servicio de inmediato: Paddle mantiene la
suscripción `active` hasta la fecha y recién ahí manda `canceled`.

**Cosas que el webhook resuelve solo:**

- Paddle puede mandar el mismo evento dos veces: se registra cada `event_id`
  en `eventos_pago` y el repetido se ignora.
- Paddle no garantiza el orden: un evento que ocurrió antes que el último
  aplicado no pisa el estado.
- Un evento sin firma válida se rechaza con 400. Un evento que no se puede
  asociar a un cliente se guarda en `eventos_pago` como `sin_cliente` para
  revisarlo a mano.

Todo lo que entra queda en la tabla `eventos_pago`, con el cuerpo completo.
Es lo primero que hay que mirar si un cliente dice que pagó y no le llega
nada:

```bash
sqlite3 /datos/pulserival.db \
  "SELECT recibido_en, tipo, cliente_id, resultado, detalle FROM eventos_pago ORDER BY recibido_en DESC LIMIT 20"
```

**`clientes.yaml` ya no manda sobre los que pagan por Paddle.** Para un
cliente con suscripción de Paddle, `aplicar-config` no toca su estado, su
ciclo ni su pasarela, y no lo desactiva por no estar en el YAML.

## 5. El plan Custom, a mano

No tiene checkout ni automatización de alta. El flujo es:

1. Conversación con la agencia (el botón "Conversemos" de la landing lleva a
   WhatsApp o al correo de `config/landing.yaml`).
2. En el panel de Paddle, cree un precio recurrente con el monto y la
   frecuencia de cobro acordados, y una transacción con ese precio. Paddle
   le da un link de pago con la forma
   `https://<su-dominio>/pagar?_ptxn=txn_...`. Guarde el `txn_...`.
3. Registrar al cliente:

   ```bash
   python -m pulserival.cli clientes agregar \
     --empresa "Agencia Norte" --email cuentas@agencia.test \
     --plan custom --cadencia-dias 2 --precio 300 \
     --paddle-transaccion txn_01abc...
   ```

   Queda en `pendiente_pago`. Cuando la agencia paga el link, Paddle avisa
   con ese `txn_...`, el webhook lo encuentra y lo activa. Las renovaciones
   siguen solas.

4. Cargar sus competidores con `competidores agregar`, como siempre. Un
   cliente sin competidores no genera reporte aunque esté pagando.

Si el link se arma después, se agrega con
`clientes editar --id <id> --paddle-transaccion txn_...`. Sin ese id el
webhook no sabe que el pago es de este cliente: queda como `sin_cliente` en
`eventos_pago` y hay que activarlo con `clientes activar`.

### Limitación conocida: la cadencia del Custom

**Hoy el sistema no cumple una cadencia menor a 7 días.** El ciclo corre
cada `PULSERIVAL_CICLO_CADA_DIAS` (7 por defecto) para **todos** los
clientes juntos. Un Custom "cada 2 días" registrado hoy recibe un reporte
por semana.

La única forma de cumplirlo sin código nuevo es `PULSERIVAL_CICLO_CADA_DIAS=1`,
que recolecta **todos** los clientes todos los días: unas 7 veces el gasto de
Apify. La solución real es la cola de trabajos por cliente con fecha límite,
que quedó fuera de este cambio. **No venda un Custom de menos de 7 días hasta
que exista.** El CLI avisa cuando se registra uno.

## 5.5 Probar el flujo completo en sandbox, paso a paso

De la persona que entra a la página al reporte en su correo. Los precios de
sandbox ya están en `config/planes.yaml`
(`pri_01m5e45724vbn47qn7gyxbggzp` mensual, `pri_01m5e47k5y0y4ajxme9b4h5wjh`
anual).

**El formulario está en modo de prueba**
(`aplicar.campos_obligatorios: false` en `config/landing.yaml`): no exige
nada para avanzar ni para enviar. Sin nombre de empresa se guarda "Empresa de
prueba" y sin correo el reporte va a `soporte@pulserival.com`. Lo único que
sigue pidiendo es la casilla de aceptación, porque es la constancia del
consentimiento. **Volver a `true` antes de lanzar.**

1. **Variables en Railway:** `PULSERIVAL_COBRO=paddle`,
   `PADDLE_ENTORNO=sandbox`, `PADDLE_CLIENT_TOKEN` (el `test_...` del panel de
   sandbox), `PADDLE_WEBHOOK_SECRET` (el del destino de notificaciones), y las
   de siempre: `PULSERIVAL_SECRET`, `PULSERIVAL_PANEL_CLAVE`, `APIFY_TOKEN`,
   `GEMINI_API_KEY`, `RESEND_API_KEY`, `EMAIL_REMITENTE`, `EMAIL_RESPONDER_A`.
2. **En el panel de sandbox de Paddle:** el destino de notificaciones
   apuntando a `https://<su-dominio>/webhooks/paddle` con los eventos de la
   sección 3, y el link de pago por defecto en `https://<su-dominio>/pagar`.
3. **`/salud` tiene que responder 200.** Si responde 500, la lista dice qué
   falta.
4. **Llenar el formulario.** Para que salga un reporte hace falta **al menos
   un competidor** (paso 3) con su página de Facebook o su dominio: sin
   competidores el ciclo no le genera reporte a nadie, a propósito. Poner su
   propio correo si quiere recibirlo ahí.
5. **Pagar** en el checkout con la tarjeta de prueba que indica la
   documentación de Paddle para sandbox. `/gracias` tiene que pasar sola a
   "activa" en unos segundos.
6. **Ver que entró:**
   `railway ssh -- python -m pulserival.cli clientes lista` muestra el cliente
   en `activa`, con su `sub_...`. El número de la primera columna es su id.
7. **Generar el reporte ya**, sin esperar al ciclo semanal:
   `railway ssh -- python -m pulserival.cli ciclo --cliente <id>`. Esto sí
   consulta Apify y usa la IA de verdad.
8. **Revisar y enviar desde el panel** (`/panel`): abrir el borrador,
   corregir si hace falta y aprobarlo. Para enviarlo, el panel pide escribir
   el nombre del cliente tal cual ("Empresa de prueba" si lo dejó vacío).
9. **Confirmar que llegó** al correo del formulario (o a soporte, si lo dejó
   vacío).

## 6. Lo que falta probar antes de cobrar de verdad

Nada de esto está probado. Los tests usan eventos armados a mano con la
misma firma que usa Paddle, pero **ningún evento real de Paddle llegó todavía
al servidor.** En este orden:

**En sandbox** (tarjeta de prueba de Paddle, no se cobra nada):

- [ ] `/salud` en 200 con `PULSERIVAL_COBRO=paddle` y `PADDLE_ENTORNO=sandbox`.
- [ ] Mandar un evento de prueba al destino desde el panel de Paddle (la
      sección de notificaciones permite simular eventos). Tiene que aparecer
      en `eventos_pago` (como `sin_cliente`, porque es un cliente inventado).
      Si el servidor responde 400, el secreto está mal.
- [ ] Alta completa por la web con el plan **mes a mes**: formulario →
      checkout → pago → `/gracias` pasa a "activo" sola. Confirmar en
      `clientes lista` el estado `activa`, ciclo `mensual` y el `sub_...`.
- [ ] Lo mismo eligiendo **Todo el año** en el checkout: ciclo `anual`, $240.
      Es la prueba de que el webhook anota el ciclo según lo que se cobró:
      el alta entra como `mensual` y tiene que terminar en `anual`.
- [ ] Una **tarjeta rechazada** (busque en la documentación de Paddle las
      tarjetas de prueba del sandbox que se rechazan): el cliente tiene que
      quedar en `pendiente_pago`.
- [ ] **Cancelar** la suscripción desde el panel de Paddle, efectiva de
      inmediato: el cliente pasa a `cancelada` y deja de entrar al ciclo.
- [ ] Un **Custom** con link de pago hecho a mano y `--paddle-transaccion`:
      al pagarlo, se activa solo.
- [ ] Una **renovación**: confirmar que llega `transaction.completed` y el
      cliente sigue `activa`. Con el precio mensual eso es esperar un mes; para
      no esperar, cree en sandbox un precio de prueba que cobre cada día.
- [ ] Un **cobro fallido en la renovación**: tiene que pasar a `vencida`.
      Mismo truco: un precio diario y una tarjeta que se rechace.

**En producción**, con el dominio ya aprobado:

- [ ] Una compra real con una tarjeta propia, en el plan mensual, usando la
      landing tal como queda publicada.
- [ ] Confirmar que el dinero llega a la cuenta de Paddle y cuánto se queda
      Paddle de comisión en un cobro de $25 y en uno de $240. El argumento
      del anual depende de esa diferencia.
- [ ] **El segundo cobro, un mes después.** Es la única prueba que no se
      puede hacer el mismo día, y la única que distingue una suscripción de
      un pago único.
- [ ] Hacer el reembolso de esa compra desde Paddle, para saber cómo se hace
      antes de necesitarlo. Ojo: **un reembolso no desactiva al cliente**
      (el webhook no escucha `adjustment.*`). Si se reembolsa, hay que
      cancelar también la suscripción.
- [ ] Bajar una copia de la base y confirmar que `eventos_pago` tiene los
      eventos de esa compra.

## 7. Lo legal: las tres páginas

Los textos están en `config/legal.yaml` y se publican en `/terminos`,
`/reembolsos` y `/privacidad` (y como `terminos.html`, etc., en la landing
estática). Se escribieron contra dos cosas verificables: lo que Paddle pide
para aprobar el dominio y la Ley 8968 de Costa Rica. **Son un borrador, no
asesoría legal: antes de publicar, que los lea un abogado.**

**Lo que falta cargar y bloquea el lanzamiento** (en `config/legal.yaml`,
`responsable:`): la razón social o su nombre legal completo, la cédula y la
dirección. El correo ya está: `soporte@pulserival.com`, el mismo de
`config/landing.yaml`. Mientras falten, las páginas los muestran
marcados como "pendiente", `cli landing` lo avisa y `/salud` también, si
`PADDLE_ENTORNO=production`.

**La política de reembolso, y por qué es así.** El criterio es: se devuelve
cuando el cliente cambió de opinión al principio, o cuando fallamos
nosotros. No se devuelve un mes ya en curso por haberse olvidado de cancelar:
para eso está la cancelación, que corta el siguiente cobro.

| Caso | Qué pasa | Por qué |
|---|---|---|
| Primer pago, dentro de 14 días | Devolución completa, sin preguntas | Paddle exige entre 14 y 90 días; la ley de Costa Rica da 8 de retracto. En 14 días ya le llegó el primer reporte. |
| Pagó el año, dentro de 14 días (primer pago o renovación) | Devolución completa | El anual es mucha plata para una renovación automática que se le pasó. |
| Un mes pagado termina sin reporte, por culpa nuestra | Se devuelve ese mes | Es lo único que el cliente compra. |
| Renovación mensual ya cobrada | No se devuelve; puede cancelar el siguiente | Igual recibe el reporte de ese mes. |
| Segunda suscripción después de un reembolso | Sin garantía | Evita suscribirse y pedir el reembolso para sacar reportes gratis. |
| Un competidor no pautó, o una plataforma no publica un dato | No es una falla | El reporte lo dice, y eso también es información. |

Lo que son decisiones suyas, no requisitos (están marcadas con `DECISIÓN` en
el YAML y se cambian ahí): los 14 días (pueden ser hasta 90), los 12 meses de
retención tras la baja y los 30 días de aviso antes de subir un precio.

**Cómo se hace un reembolso, en orden:**

1. En el panel de Paddle, reembolsar la transacción. Hay que tramitarlo
   dentro de los 8 días naturales del pedido: es lo que dice la política.
2. Cancelar la suscripción en Paddle. **Un reembolso no la cancela ni
   desactiva al cliente**: el webhook no escucha los reembolsos
   (`adjustment.*`). La cancelación sí llega y lo pasa a `cancelada`.

**Pedidos de datos (Ley 8968).** Si alguien pide ver, corregir o borrar sus
datos, hay **5 días hábiles** para responder. Para borrar a un cliente de
verdad:

```bash
python -m pulserival.cli clientes eliminar --id <id>              # muestra qué se borraría
python -m pulserival.cli clientes eliminar --id <id> --confirmar  # lo borra
```

Borra al cliente, sus competidores, sus anuncios, sus reportes y los
archivos de reportes en disco. No deja borrar a alguien con una suscripción
de Paddle activa (primero se cancela). Los avisos de pago se conservan sin el
cliente, por contabilidad. Las copias de respaldo lo siguen teniendo hasta
que roten.

La política promete borrar los datos a los 12 meses de la baja. **El sistema
no lo hace solo**: hay que revisar `clientes lista` de vez en cuando y
eliminar a mano a quien se dio de baja hace más de un año.

**Para revisar con el abogado o el contador, que este código no resuelve:**

- Si la base de clientes tiene que inscribirse ante la Prodhab. La Ley 8968
  (art. 21) lo exige para bases "con fines de distribución, difusión o
  comercialización"; una base de clientes de uso interno no parece serlo,
  pero es una lectura, no una certeza.
- La cláusula que limita la responsabilidad a lo pagado en los últimos 12
  meses, y la de tribunales de Costa Rica.
- Cómo se declaran en Costa Rica los ingresos que Paddle le transfiere.
- Si el plan de Gemini que usa es el gratuito: en ese caso Google puede usar
  lo que se le envía para mejorar sus productos, y la Política de
  privacidad no lo dice. Con un plan pago no pasa.
- Si la rama `datos` del repositorio de GitHub sigue guardando la base con
  datos de clientes: GitHub no está en la lista de proveedores de la
  política. O se agrega, o se deja de guardar ahí.

## 8. Cómo se opera, día a día

**Ver quién pagó y en qué estado está:**

```bash
python -m pulserival.cli clientes lista
```

Muestra plan, periodicidad, cadencia, ciclo, estado y el estado que reporta
Paddle.

**Activar a mano** (un pago que llegó por otra vía, o un evento `sin_cliente`):

```bash
python -m pulserival.cli clientes activar --id <id> --referencia "txn_..."
```

**Dar de baja a alguien que paga por Paddle:** cancelar la suscripción en el
panel de Paddle. El webhook lo pasa a `cancelada`. Desactivarlo solo en la
base no detiene los cobros.

**Dar de baja a alguien sin Paddle:**

```bash
python -m pulserival.cli clientes editar --id <id> --activo 0
```

Se desactiva, no se borra: el historial de anuncios es lo que permite decir
"esto es nuevo" si vuelve. Para borrarlo de verdad, ver
`clientes eliminar` en la sección 7.

## Fuentes

Lo que este documento y el código dicen sobre Paddle sale de su
documentación:

- Firma de los webhooks: <https://developer.paddle.com/webhooks/signature-verification>
- Responder a tiempo y reintentos: <https://developer.paddle.com/webhooks/about/respond-to-webhooks>
- Entrega, duplicados y orden: <https://developer.paddle.com/webhooks/about/how-webhooks-work>
- `subscription.created`: <https://developer.paddle.com/webhooks/subscriptions/subscription-created>
- `customData`: <https://developer.paddle.com/build/transactions/custom-data/>
- `Paddle.Checkout.open()`: <https://developer.paddle.com/paddle-js/methods/paddle-checkout-open/>
- `Paddle.Environment.set()`: <https://developer.paddle.com/paddle-js/methods/paddle-environment-set/>
- Link de pago por defecto: <https://developer.paddle.com/build/transactions/default-payment-link>
- Aprobación del dominio: <https://www.paddle.com/help/start/account-verification/what-is-domain-verification>
- Por qué se rechaza un dominio (nombre legal en los Términos, páginas enlazadas desde la navegación): <https://www.paddle.com/help/start/account-verification/why-has-my-domain-been-rejected>
- Mínimos de Términos, Reembolso y Privacidad para vender con Paddle, incluida la frase del Merchant of Record y la ventana de 14 a 90 días: <https://developer.paddle.com/embedded-billing-partner-skills/.well-known/skills/draft-seller-policies/SKILL.md>
- Política de reembolso de Paddle hacia el comprador: <https://www.paddle.com/legal/refund-policy>
- Términos de compra de Paddle: <https://www.paddle.com/legal/checkout-buyer-terms>

Y sobre la ley de Costa Rica:

- Ley 8968, Protección de la Persona frente al Tratamiento de sus Datos Personales (arts. 5, 7, 14 y 21): <https://transparencia.munigoicoechea.go.cr/wp-content/uploads/2021/07/No-8968-PROTECCION-DE-LA-PERSONA-FRENTE-AL-TRATAMIENTO-DE-SUS-DATOS-PERSONALES.pdf>
- Retracto de 8 días en el comercio electrónico (Ley 7472, art. 40, y Decreto 40703-MEIC): <https://www.theworldlawgroup.com/membership/news/news-application-of-the-right-of-withdrawal-in-costa-rica-convenience-and-comparative-analysis-with-spanish-legislation>
