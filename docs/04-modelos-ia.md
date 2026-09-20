# 04 · Qué modelo de IA para cada tarea

## La idea en una frase

Separá las tareas de **volumen** (leer y clasificar cada anuncio: muchas
llamadas, texto corto, no las ve el cliente) de la tarea de **calidad**
(redactar el reporte: una llamada por reporte, y es lo único que el cliente
lee). Pagá bien solo la segunda.

## La recomendación, con lo que ya tenés

Tenés Groq con tier gratuito y ~$9 de crédito en Gemini. Eso alcanza para
mucho más de lo que parece.

| Tarea | Proveedor | Modelo | Por qué |
|---|---|---|---|
| Analizar cada anuncio (ángulo, oferta, precios, urgencia) | **Groq** | `llama-3.3-70b-versatile` | es la tarea de volumen; el tier gratuito de Groq la cubre y es rapidísimo. Salida JSON, no prosa: no necesita el mejor modelo del mundo |
| Redactar el reporte final | **Gemini** | `gemini-2.5-pro` | es lo único que ve el cliente. Acá la calidad de escritura en español se nota, y es **una sola llamada por reporte** |
| Etiquetar tus ediciones (Fase 2) | **Groq** | `llama-3.1-8b-instant` | tarea trivial, volumen mínimo, el modelo más barato alcanza |
| Respaldo de todo | **stub** (sin IA) | — | si Groq y Gemini fallan, el borrador se arma con reglas. Nunca te quedás sin nada que revisar |

Esto ya está configurado en `config/modelos.yaml`. El orden de la lista de
cada tarea es el orden en que se intenta: si el primero falla (sin clave,
límite de cuota, red caída), se pasa al siguiente automáticamente.

## Cuánto te va a costar de verdad

Cuentas con un cliente de 3 competidores, cadencia semanal:

**Analizar anuncios** (Groq, tier gratuito): unos 10-15 anuncios nuevos o
cambiados por semana, ~600 tokens de entrada y ~250 de salida cada uno. En el
tier gratuito de Groq, **$0**. Si algún día lo pagás, son fracciones de
centavo por reporte.

**Redactar el reporte** (Gemini 2.5 Pro): el prompt con 15 anuncios ronda los
4.000 tokens de entrada y 1.200 de salida.

- entrada: 4.000 / 1.000.000 × $1,25 ≈ **$0,005**
- salida: 1.200 / 1.000.000 × $10,00 ≈ **$0,012**
- **≈ $0,017 por reporte** (menos de 2 centavos)

O sea: con $9 de crédito de Gemini tenés del orden de **500 reportes**. Un
cliente semanal consume ~$0,90 al año. Esto no es un costo, es ruido en tu
estructura: el costo real del producto es el scraper (ver
[02 · Fuentes de datos](02-fuentes-de-datos.md)) y tu tiempo de revisión.

Para verlo en cualquier momento, con datos reales de tus corridas:

```bash
python3 -m pulserival.cli costos
```

## Si querés gastar todavía menos

En `config/modelos.yaml`, cambiá el primer candidato de `redactar_reporte` a
`gemini-2.5-flash` (unas 4 veces más barato que Pro). Mi recomendación: **no
lo hagas todavía**. En Fase 1 el reporte es tu producto y estás aprendiendo
qué prompt funciona; ahorrar un centavo por reporte a cambio de más edición
tuya es un mal cambio. Cuando tengas el prompt afinado (Fase 2), probá Flash
y comparalo con el indicador que ya tenés: si la similitud
(`cli dataset`) no baja, quedate con Flash.

## Cómo cambiar de modelo o de proveedor

Todo vive en `config/modelos.yaml`:

```yaml
tareas:
  redactar_reporte:
    - proveedor: gemini
      modelo: gemini-2.5-pro     # ← cambiá esto
      temperatura: 0.4
      max_tokens: 4000
    - proveedor: groq            # ← si Gemini falla, se usa este
      modelo: openai/gpt-oss-120b
    - proveedor: stub            # ← último recurso, sin IA
```

Ninguna parte del código menciona "Gemini" o "Groq" salvo los dos archivos de
proveedor (`pulserival/ia/gemini_proveedor.py`, `groq_proveedor.py`) y ese
YAML. Agregar un proveedor nuevo (Anthropic, OpenAI, DeepSeek, lo que sea) es
escribir una clase con dos métodos —`disponible()` y `generar()`— y registrarla
en `pulserival/ia/router.py`. Unas 40 líneas, copiando cualquiera de los dos
existentes como molde.

Los precios para estimar costo también están en ese YAML, en
`precios_usd_por_millon`. **Verificalos cada tanto**, cambian seguido:

- Gemini: <https://ai.google.dev/gemini-api/docs/pricing>
- Groq: <https://console.groq.com/docs/models>

(La tabla del YAML se verificó el 2026-09-20. Si un modelo no está en la
tabla, el costo se registra como $0: no se rompe nada, pero tu control de
gasto queda ciego. Agregalo.)

## El tope de gasto

```yaml
tope_gasto_usd_por_corrida: 0.75
```

Si una corrida va a pasar de ahí, se detiene y te avisa en vez de quemarte el
crédito en silencio. Con los números de arriba, una corrida normal de un
cliente gasta ~$0,02: el tope solo se activa si algo anda mal (por ejemplo,
un scraper que de golpe devuelve 500 anuncios). Es un fusible, no un límite
de trabajo.

## Dónde se controla la calidad del texto

Dos lugares, ninguno de los dos es el modelo:

1. **El prompt** (`pulserival/ia/prompts/redactar_reporte.md`): tiene las
   reglas de honestidad (no inventar métricas, separar hecho de
   interpretación, citar la referencia de cada anuncio) y la estructura del
   reporte. Es un archivo de texto: editalo cuando quieras.
2. **El validador** (`pulserival/reporte/validar.py`): después de generar,
   revisa que el borrador no mencione datos que no existen, que no cite
   referencias inventadas y que no se haya dejado afuera ningún anuncio nuevo
   o cambiado. Ese resultado aparece arriba del borrador que editás.

Un modelo más caro no arregla un prompt flojo. Cambiá el prompt primero.
