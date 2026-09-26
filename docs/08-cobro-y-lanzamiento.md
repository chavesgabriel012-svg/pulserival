# 08 · Cobro y lanzamiento

Este documento es la lista de lo que falta antes de cobrarle a una persona
real, y de lo que **no** se puede dar por probado porque todavía no pasó
plata de verdad por el sistema.

## La decisión: arranque híbrido

Hay tres formas de cobrar, y se eligió la del medio:

| | Manual | **Híbrido (elegido)** | Autoservicio |
|---|---|---|---|
| Landing pública | opcional | **sí** | sí |
| Checkout en línea | no | **sí, alojado por la pasarela** | sí |
| Quién activa al cliente | usted | **usted, a mano** | el webhook, solo |
| Servidor propio | no | **no** | sí |
| Costo de infraestructura | $0 | **$0** | ~$5-20/mes |

**Por qué el híbrido no necesita servidor.** El Hosted Payment Page de
Tilopay exige backend: su servidor pide la URL de pago a la API y recibe el
resultado en un callback. Pero las dos pasarelas tienen además un producto
de enlaces de cobro que se crean **una vez desde el panel** y son URLs
reutilizables: la ruta *No-Code* de Tilopay y *ONVO Link*. Esos enlaces se
pegan en `config/planes.yaml` y la landing los usa tal cual.

Lo que queda manual es el paso de después del pago: la pasarela le avisa a
usted (correo, su panel), usted confirma, y activa al cliente con un comando.
Automatizar ese paso es lo único que obliga a tener un servidor encendido,
porque un webhook necesita algo escuchando las 24 horas.

## Lo que ya está construido

- Los cuatro planes, en `config/planes.yaml`: prueba gratis, mensual ($20),
  semanal ($100) y a la medida.
- La base de datos guarda plan, estado de suscripción, cadencia en días,
  precio acordado, pasarela y referencia del pago.
- La prueba gratis genera **un** reporte y no vuelve a generar. Tampoco
  gasta scraper después de usarla.
- El plan a la medida acepta cualquier cadencia (`--cadencia-dias 10`).
- Un cliente en `pendiente_pago` no recibe reportes ni consume Apify.
- La landing (`python3 -m pulserival.cli landing`), con el formulario de
  alta que llega por WhatsApp.

## Lo que falta antes de cobrar

### 1. Elegir la pasarela y crear los enlaces

Todavía no está decidido entre Tilopay y ONVO. Para el híbrido lo único que
importa es que ambas permiten enlaces de cobro sin código:

- **Tilopay** — ruta No-Code: <https://tilopay.com/developers/sin-codigo>
- **ONVO** — ONVO Link: <https://onvopay.com/en/onvo-link>

Cree un enlace por plan de pago (mensual y semanal), péguelo en
`config/planes.yaml` en el campo `enlace_pago`, y regenere la landing.
Mientras el campo esté vacío el botón dice "Hablemos" en vez de cobrar.

**Importante para suscripciones:** verifique al crear el enlace que sea de
**cobro recurrente** y no de pago único. Un enlace de pago único le cobra al
cliente una sola vez y el segundo mes no llega nada — y usted se entera
cuando el cliente ya recibió cuatro reportes gratis.

### 2. Configurar el contacto

En `config/landing.yaml`, el WhatsApp (solo dígitos con código de país) o el
correo. Sin uno de los dos el formulario no tiene a dónde mandar los datos.

### 3. Probar con una transacción real

Nada de lo de abajo está probado: **ninguna transacción pasó por el sistema
todavía.** Antes del primer cliente que pague:

- [ ] Hacer una compra real de $1 (o el mínimo que permita la pasarela) con
      una tarjeta propia, usando el enlace tal como queda en la landing.
- [ ] Confirmar que el cobro llega al panel de la pasarela.
- [ ] Confirmar que **el segundo cobro se ejecuta solo** al mes siguiente.
      Esto no se puede verificar el mismo día: es la única prueba que
      requiere esperar un ciclo completo, y es la que distingue una
      suscripción de un pago único.
- [ ] Verificar cuánto cobra la pasarela por transacción y si hay mínimo
      mensual. Con el plan de $20, una comisión alta se come el margen.
- [ ] Probar el reembolso de esa compra, para saber cómo se hace antes de
      necesitarlo con un cliente molesto.
- [ ] Confirmar qué pasa cuando una tarjeta es rechazada: si la pasarela
      reintenta, cuántas veces, y cómo se entera usted.

### 4. Lo legal y lo fiscal

Fuera del alcance de este código, pero bloquea el lanzamiento igual:
facturación electrónica, condiciones del servicio, y qué pasa con los datos
del cliente si se da de baja.

## Cómo se opera, día a día

**Cuando alguien pide la prueba gratis** (llega por WhatsApp desde la
landing):

```bash
python3 -m pulserival.cli clientes agregar \
  --empresa "Cafetería X" --email persona@ejemplo.com \
  --plan prueba --industria "cafeterías" \
  --notas "Contexto que mandó por WhatsApp, 3-4 líneas"

python3 -m pulserival.cli competidores agregar --cliente <id> \
  --nombre "Competidor 1" --meta-pagina "https://facebook.com/..." \
  --google-dominio "competidor.co.cr"
```

Antes de confirmarle nada, verifique que el competidor sí esté pautando:

```bash
python3 -m pulserival.cli prueba-scraper --consulta "Competidor 1" --limite 10
```

Si devuelve cero, avísele al cliente de entrada en vez de que se entere
cuando le llegue un reporte vacío.

**Cuando alguien paga:** la pasarela le avisa. Usted confirma en su panel y
activa:

```bash
python3 -m pulserival.cli clientes activar --id <id> \
  --referencia "ONVO sub_abc123" --pago-proveedor onvopay
```

Hasta que corra eso, el cliente queda en `pendiente_pago` y **no** recibe
reportes ni consume scraper. Eso es a propósito: nadie recibe el producto
antes de pagarlo.

**Cuando alguien se da de baja:**

```bash
python3 -m pulserival.cli clientes editar --id <id> --activo 0
```

Se desactiva, no se borra: el historial de anuncios es lo que permite decir
"esto es nuevo" si vuelve.

## Cuándo conviene pasar al servidor

Cuando activar a mano empiece a doler. Concretamente:

- más de 10-15 clientes, o
- varias altas por semana, o
- alguien más además de usted operando el sistema.

Ahí se levanta el servidor (Railway, ~$5/mes), se mueve la base al disco
persistente, y se agregan las dos cosas que hoy no existen: el webhook que
activa solo y el panel web. El pipeline no cambia: `config.ruta_db()` ya lee
`PULSERIVAL_DB`, así que apunta al disco nuevo sin tocar código.

Mientras tanto, el cron sigue en GitHub Actions, que funciona igual con 1
cliente que con 50.
