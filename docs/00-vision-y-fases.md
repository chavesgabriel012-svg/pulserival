# 00 · Visión y fases

## Qué vende PulseRival

No vende datos de anuncios. Los datos están públicos y gratis: cualquiera
puede abrir la Biblioteca de Anuncios de Meta y mirar. Lo que vende es
**que alguien los mire todas las semanas, note qué cambió, y le diga a un
dueño de negocio qué significa**. Eso último es el producto.

Esto define una regla de diseño que atraviesa todo el código: el reporte
interpreta. Un reporte que solo lista anuncios es un reporte que el cliente
puede hacer solo, y por eso el control de calidad automático
(`pulserival/reporte/validar.py`) revisa cobertura e interpretación, no
solo ortografía.

## Las tres fases

Los meses son referenciales. El criterio para pasar de fase no es el
calendario, es el indicador.

### Fase 1 — ahora

- La recolección es **100% automática** desde el día 1: cron, scraper, base
  de datos, detección de cambios, borrador generado. Cero revisión manual de
  sitios web.
- Vos **revisás y editás el borrador** antes de que salga. Ahí entra tu
  criterio: lo que sabés del cliente por conversación, el tono, la
  recomendación final.
- Cada edición se guarda estructurada: borrador, versión final, diff,
  etiqueta y razón.

Lo que el sistema ya hace solo en esta fase: traer los anuncios, deduplicar,
detectar qué cambió, escribir el borrador interpretativo, validarlo contra
reglas de honestidad, armar el email y dejar todo listo.

### Fase 2 — afinar con evidencia

`python3 -m pulserival.cli dataset` exporta `salida/dataset_ediciones.jsonl`:
un registro por reporte con el borrador, lo que enviaste, el diff, la
etiqueta y los datos de entrada exactos.

Con eso se trabaja así:

1. Leé los diffs agrupados por etiqueta. `flujo.metricas()` te dice cuáles
   se repiten (`etiquetas`).
2. El campo `bloques` te dice **qué sección reescribís siempre**. Si el 80%
   de tus ediciones están en "Qué haría yo esta semana", el problema está en
   esa parte del prompt, no en todo el prompt.
3. Cambiá `pulserival/ia/prompts/redactar_reporte.md` para atacar ese patrón.
   La versión del prompt queda guardada en cada reporte
   (`version_prompt`), así que podés comparar antes y después.

No hace falta entrenar ningún modelo. Con 20-30 diffs bien etiquetados,
reescribir el prompt rinde más que cualquier fine-tuning, y cuesta cero.

### Fase 3 — envío casi sin intervención

Se habilita cuando el indicador lo permite, no cuando llega el mes 12.
`flujo.metricas()` devuelve:

```json
{ "similitud_promedio": 0.0, "similitud_ultimos_5": 0.0, "listo_para_fase_3": false }
```

`similitud` es cuánto del borrador sobrevivió tal cual a tu edición. El
sistema marca `listo_para_fase_3: true` con **8 reportes revisados y una
similitud promedio ≥ 0.90 en los últimos 5**. En palabras: cinco reportes
seguidos en los que casi no cambiaste nada.

Cuando llegue ese momento, el cambio de código es chico y está previsto: en
`pipeline.ciclo_completo()`, agregar el envío condicionado a que el control
de calidad devuelva `aprobado: true` y sin avisos de cobertura. El "spot
check" queda como lo que es: mirás el borrador 2 minutos y si no tocás nada,
sale.

Recomendación honesta: incluso en Fase 3, mantené la revisión completa para
clientes nuevos durante sus primeras 3 semanas. El costo de un reporte malo
al principio de una relación es mucho más alto que 15 minutos tuyos.

## Lo que el sistema nunca va a hacer solo

- Afirmar cuánto invirtió un competidor, cuánto alcance tuvo o cuántos clics
  consiguió. **Esos datos no son públicos.** El validador rechaza el borrador
  si aparecen; el pie del email lo aclara al cliente.
- Decir que un anuncio "fracasó" porque dejó de aparecer. Solo dejó de
  aparecer.
- Enviar un reporte sin que exista una versión final registrada (salvo que
  se lo pidas explícito con `--forzar`).
