# 07 · ¿Alcanzan $50 para probar, dejarlo corriendo y comprar el dominio?

**Sí, y sobra.** Precios verificados el 2026-09-21; los enlaces están al final
porque esto cambia.

## Lo que hay que pagar, y lo que no

| Componente | Costo real | Nota |
|---|---|---|
| Scrapers (Apify) | **$0/mes** en el plan gratuito | trae $5 de crédito mensual; un cliente piloto consume ~$1,20 |
| Redacción con IA (Gemini) | **$0** | ya tenés $9 de crédito, y un reporte cuesta ~$0,017 |
| Análisis de anuncios (Groq) | **$0** | tier gratuito |
| Envío de correo (Resend) | **$0** | plan gratuito: 3.000 correos/mes, 100/día, hasta 3 dominios |
| Servidor / cron (GitHub Actions) | **$0** | repo privado incluye 2.000 min/mes; una corrida usa ~3 min |
| Base de datos | **$0** | es un archivo |
| **Dominio** | **$11 a $28/año** | la única cosa que hay que pagar de verdad. Ver abajo |
| **Total del primer año** | **$11 a $28** | te quedan $22 a $39 de los $50 |

O sea: el producto funcionando, con un cliente piloto real, entra en el
plan gratuito de todo. El dominio es el único gasto obligatorio.

## El dominio: elegí bien, porque acá está toda la plata

| Opción | Precio anual | Comentario |
|---|---|---|
| `.com` (Cloudflare, Namecheap, Porkbun) | **~$11** | lo que recomiendo. Barato, universal, y el cliente no duda al escribirlo |
| `.co.cr` | **$25 + IVA ≈ $28** | señal local fuerte. Vale si tu pitch es "inteligencia de mercado tico" |
| `.cr` | **$70 + IVA ≈ $79** | **se come todo el presupuesto.** No vale la pena para arrancar |
| Dominio personal `.cr` (persona física tica) | **$15 + IVA ≈ $17** | tiene restricciones de uso; no lo usaría para una marca comercial |

Mi recomendación concreta: **un `.com` a ~$11**, y guardate el resto. Si el
producto funciona y querés el `.co.cr` para reforzar lo local, lo compras en
el mes 3 con plata del primer cliente, no con capital inicial.

Comprá el dominio en un registrador que cobre al costo (Cloudflare Registrar
no le pone margen) y no en el mismo lugar donde te vendan hosting: no
necesitás hosting.

## Cómo quedan los $50

```
Dominio .com, primer año            $11
Reserva para Apify (mes 1-3)        $15   ← solo si sumás un 2.º y 3.er cliente
Colchón imprevistos                 $24
                                    ───
                                    $50
```

La "reserva para Apify" es lo único que conviene tener a mano. El plan gratuito
te da $5/mes de crédito, que alcanza para **uno o dos clientes** de 3
competidores con cadencia semanal. Al tercer cliente te pasás y necesitás el
plan Starter: **$19/mes**. Ese es el momento en que el producto tiene que
estar ya cobrando: con un solo cliente pagando $100-150/mes, el costo de
infraestructura es menos del 20%.

Para saber exactamente cuándo llega ese momento, sin adivinar:

```bash
python3 -m pulserival.cli presupuesto
```

```
Plan de Apify: free · crédito incluido $5.00/mes
Supuesto: hasta 40 anuncios por competidor por corrida (peor caso)

  Ferretería El Tornillo (semanal, 2 competidores): ~519 anuncios/mes · $2.22/mes

  Scrapers: $2.22/mes
  ✓ Entra en el crédito del plan. Te sobran $2.78/mes.
  Con este plan te caben ~2 cliente(s) de este tamaño.
```

Proyecta el peor caso: asume que cada competidor devuelve el límite completo
de anuncios en cada corrida. En la práctica va a ser menos, porque pocos
negocios en Costa Rica tienen 40 anuncios activos al mismo tiempo.

## Tres palancas si el crédito te queda corto

1. **Bajar el límite por competidor.** `recolectar --limite 25`. Un competidor
   tico promedio no tiene más de 10-15 anuncios activos; 40 es margen de
   sobra.
2. **Cadencia mensual en vez de semanal** para los clientes que la acepten:
   divide el costo por 4,3. Ojo: también reduce el valor percibido y amplía el
   punto ciego (anuncios que aparecen y se caen entre corridas).
3. **Menos competidores por cliente.** 3 bien elegidos rinden más que 6 a
   medias, y el reporte se lee mejor.

## Lo que NO cuesta dinero pero sí cuesta tiempo

Para que el presupuesto sea honesto: el costo real del piloto es **tu tiempo
de revisión**, ~20 minutos por reporte al principio. Con 4 clientes semanales
son ~5 horas al mes. Eso es lo que la Fase 2 está diseñada para bajar, y es
la razón por la que registrar cada edición con su etiqueta no es opcional.

## Fuentes de los precios

- Apify, planes y crédito mensual: <https://apify.com/pricing>
- Precio por 1.000 anuncios del scraper de Meta:
  <https://apify.com/apify/facebook-ads-scraper/pricing>
- Scraper del Centro de Transparencia de Google:
  <https://apify.com/pulsedata/google-ads-transparency-scraper>
- Resend, plan gratuito: <https://resend.com/pricing>
- Gemini: <https://ai.google.dev/gemini-api/docs/pricing>
- Groq: <https://console.groq.com/docs/models>
- Dominios `.cr` y `.co.cr` (precios oficiales): <https://dominios.cr/> y
  <https://nic.cr/>

Los precios de esta página también están en `config/fuentes.yaml`
(sección `costos`), que es de donde los lee el comando `presupuesto`. Si
cambian, actualizá ese archivo y el comando se corrige solo.
