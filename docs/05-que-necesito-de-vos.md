# 05 · Qué necesito de vos antes de la primera corrida real

El código ya está y corre (`make demo` lo prueba de punta a punta sin
necesitar nada de esto). Lo que sigue es lo que hace falta para que la primera
corrida sea **con datos reales de tu cliente piloto**.

Está ordenado por lo que bloquea más. Los puntos 1 y 2 son los únicos
imprescindibles para arrancar.

---

## 1 · Bloqueante · El cliente piloto y sus competidores

Necesito, en texto plano, esto:

**Del cliente:**
- Nombre de la empresa y a quién le llega el reporte (nombre y correo).
- Cadencia: ¿semanal o mensual? (recomiendo semanal para el piloto: más
  ciclos de aprendizaje en menos tiempo).
- Industria, en tus palabras.
- **Contexto que solo vos sabés**, 3-4 líneas: qué le preocupa, su ticket
  promedio, cuántas sedes, si compite por precio o por servicio. Esto entra
  al prompt y es lo que más sube la calidad del reporte. Sin esto el reporte
  sale correcto pero genérico.

**De cada competidor (2 a 4 para el piloto, no más):**

| Dato | Cómo conseguirlo | ¿Obligatorio? |
|---|---|---|
| Nombre como querés que aparezca en el reporte | vos decidís | sí |
| URL de su página de Facebook | abrí su página, copiá la URL de la barra | ideal |
| Término de búsqueda en la Biblioteca de Anuncios | el nombre con el que aparece en `facebook.com/ads/library` | si no tenés la URL |
| Dominio de su sitio web | `competidor.co.cr` | para Google |
| Prioridad 1, 2 o 3 | 1 = el que más le importa al cliente | ayuda |

Mínimo viable por competidor: **la página de Facebook o el término de
búsqueda** (para Meta) y **el dominio** (para Google). Con uno solo de los dos
funciona; se salta la otra plataforma y te lo avisa.

**Verificación que te pido hacer vos, 5 minutos por competidor** (y que vale
oro, porque evita una corrida vacía):

1. Abrí <https://www.facebook.com/ads/library/>, país **Costa Rica**,
   categoría **Todos los anuncios**, y buscá el competidor.
2. ¿Aparecen anuncios activos? Si no aparece nada, ese competidor **no está
   pautando ahora** y no va a generar contenido para el reporte. Decímelo:
   conviene cambiarlo por otro o advertírselo al cliente de entrada.
3. Lo mismo en <https://adstransparency.google.com/> con el nombre o el
   dominio.

Un piloto donde ninguno de los competidores pauta es el peor arranque
posible, y se detecta en 15 minutos.

---

## 2 · Bloqueante · Las claves (10 minutos)

| Clave | Dónde | Para qué |
|---|---|---|
| `APIFY_TOKEN` | <https://console.apify.com/account/integrations> | los scrapers de Meta y Google |
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | redacción del reporte (tu crédito de $9) |
| `GROQ_API_KEY` | <https://console.groq.com/keys> | análisis de anuncios (tier gratuito) |

Van en el archivo `.env` (copiá `.env.example`). Sin ellas el sistema corre en
modo demo: no falla, pero usa datos de ejemplo.

En Apify, además: activá el plan que uses y revisá el costo por 1.000
resultados del actor, para saber tu costo real por cliente.

---

## 3 · Antes del primer envío · El correo

Decidime una de las dos:

- **Resend** (recomendado): cuenta en <https://resend.com>, verificás tu
  dominio (3 registros DNS: SPF, DKIM y opcionalmente DMARC) y sacás la clave.
  Toma 20 minutos más la propagación del DNS. Te da reportes de entrega y
  rebotes, que es lo que querés cuando le mandás a clientes que pagan.
- **Tu correo actual por SMTP**: más rápido de configurar, sin dominio nuevo,
  pero sin visibilidad de entregabilidad.

Además necesito: la dirección `De:` que querés usar
(`reportes@tudominio.com`) y si las respuestas van a otra
(`EMAIL_RESPONDER_A`).

> Mientras tanto, `reporte enviar --simular` genera el HTML en `salida/` sin
> mandar nada. Podés hacer el piloto completo así y mandar el primer reporte
> pegándolo a mano desde tu correo.

---

## 4 · Decisiones de producto que son tuyas, no técnicas

1. **Formato de entrega.** El email HTML está armado y responde bien en
   celular; incluye un anexo con cada anuncio linkeado a su ficha pública.
   ¿Querés además el resumen de WhatsApp (ya se genera, en
   `salida/*-whatsapp.txt`)? ¿Querés PDF adjunto? El PDF es trabajo extra y
   mi recomendación es no hacerlo hasta que un cliente lo pida.
2. **Día y hora de envío.** El cron está en lunes 5 a.m. Costa Rica para que
   el borrador te espere temprano. Vos decidís qué día sale al cliente:
   martes en la mañana funciona bien (el lunes está saturado).
3. **Marca.** El email dice "PulseRival" con un encabezado sobrio. Si tenés
   logo y colores, mandámelos y los pongo. Si el producto va en blanco (bajo
   la marca de una agencia), decímelo ahora: cambia el diseño del email.
4. **Qué pasa si una semana no hay movimiento.** Hoy el sistema genera un
   reporte honesto y corto que dice "no hubo movimiento y eso también es
   información". Alternativa: no enviar y avisar por WhatsApp. **Recomiendo
   enviarlo igual**: la constancia es la mitad del valor de un producto por
   suscripción, y un reporte corto refuerza que no inflás contenido.
5. **Precio y qué prometés.** No es mi decisión, pero sí una advertencia
   técnica: no prometas datos de inversión, alcance ni resultados. No son
   públicos y ningún proveedor los tiene. Vender la interpretación —no los
   datos— es además tu mejor argumento de venta.

---

## 5 · Lo que yo necesito de vuelta después de la primera corrida

Para que la Fase 2 sirva, cuando edités el primer borrador, registralo con
etiqueta y razón:

```bash
python3 -m pulserival.cli reporte registrar --id 1 \
  --etiqueta recomendacion_debil \
  --razon "Las recomendaciones eran genéricas; las cambié por dos concretas"
```

Etiquetas disponibles: `tono`, `dato_incorrecto`, `dato_faltante`, `recorte`,
`reordenamiento`, `recomendacion_debil`, `jerga`, `contexto_cliente`,
`formato`, `otro`.

Esa etiqueta de dos palabras es lo que dentro de dos meses te dice qué
arreglar en el prompt. Si te salteás este paso, la Fase 2 no existe.

---

## Checklist para copiar y pegar

```
[ ] Cliente piloto: empresa, contacto, correo, cadencia, industria
[ ] Contexto del cliente (3-4 líneas en tus palabras)
[ ] Competidor 1: nombre + página de Facebook + dominio + prioridad
[ ] Competidor 2: nombre + página de Facebook + dominio + prioridad
[ ] Competidor 3 (opcional)
[ ] Verificado en facebook.com/ads/library que sí están pautando hoy
[ ] Verificado en adstransparency.google.com
[ ] APIFY_TOKEN en .env
[ ] GEMINI_API_KEY en .env
[ ] GROQ_API_KEY en .env
[ ] Decidido: Resend o SMTP
[ ] Decidido: día de envío al cliente
[ ] Decidido: ¿WhatsApp sí o no?
[ ] Logo y colores (o confirmar que va sin marca)
```
