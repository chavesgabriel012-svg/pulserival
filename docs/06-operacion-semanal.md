# 06 · Operación semanal y qué hacer cuando algo falla

## Tu rutina (20 minutos por cliente, y va a bajar)

**Lunes 5:00 a.m. — el sistema, solo**

Corre la recolección, detecta qué cambió, genera el borrador, lo valida y lo
deja en `borradores/`. No te avisa porque no hace falta: está ahí cuando
llegás.

**Lunes en la mañana — vos**

```bash
python3 -m pulserival.cli reporte lista        # ver qué hay
```

1. Abrí el `.md` de `borradores/`. Arriba tiene el resultado del control de
   calidad: leelo primero.
   - `✗ dato_inventado` o `✗ referencia_inexistente`: **arreglalo sí o sí**,
     son errores que el cliente puede detectar.
   - `! cobertura_incompleta`: el borrador ignoró un anuncio nuevo. Decidí si
     vale la pena mencionarlo.
   - `! tono`, `! muy_largo`: criterio tuyo.
2. Editá. Mirá especialmente la sección "Qué haría yo esta semana": es donde
   tu criterio vale más y donde el modelo es más flojo.
3. Registrá tu versión final **con etiqueta y razón**:

```bash
python3 -m pulserival.cli reporte registrar --id 3 \
  --etiqueta contexto_cliente --razon "Agregué que están por abrir sede en Heredia"
```

4. Mirá cómo quedó y mandalo:

```bash
python3 -m pulserival.cli reporte enviar --id 3 --simular   # abrí el HTML de salida/
python3 -m pulserival.cli reporte enviar --id 3
```

**Durante la semana — cuando el cliente reacciona**

```bash
python3 -m pulserival.cli feedback agregar --cliente 1 --reporte 3 \
  --tipo pregunta --canal whatsapp \
  --texto "Preguntó si el competidor bajó el precio en todas las sedes"
```

Esto no es burocracia: las preguntas que hace el cliente te dicen qué sección
del reporte le importa de verdad. Es la otra mitad del dataset.

**Una vez al mes**

```bash
python3 -m pulserival.cli dataset    # ¿estoy editando cada vez menos?
python3 -m pulserival.cli costos     # ¿cuánto llevo gastado en IA?
```

Si `similitud_ultimos_5` sube reporte a reporte, el prompt está aprendiendo de
tus ediciones. Si se queda clavada, mirá el campo `bloques` del dataset: te
dice exactamente qué sección reescribís siempre, y eso es lo que hay que
cambiar en `pulserival/ia/prompts/redactar_reporte.md`.

---

## Cuando algo falla

### "El reporte salió vacío / no detectó nada"

1. ¿Corrió la recolección? `python3 -m pulserival.cli recolectar --cliente 1`
   y mirá la salida.
2. Si dice `saltado: el competidor no tiene datos configurados`, falta la
   página de Meta o el dominio de Google:
   `competidores editar --id 2 --meta-pagina "https://..."`.
3. Si trajo 0 anuncios sin error, verificá a mano en
   `facebook.com/ads/library` (país Costa Rica) que el competidor esté
   pautando. Muchas veces la respuesta es que de verdad no hay nada — y eso
   es información válida para el cliente.

### "Apify devolvió error"

| Error | Qué hacer |
|---|---|
| `APIFY_TOKEN inválido (401)` | regenerá el token en la consola de Apify |
| `límite de crédito (402)` | se agotó el plan del mes |
| `Apify respondió 404` | cambió el nombre del actor: corregilo en `config/fuentes.yaml` |
| trae anuncios pero llegan vacíos | el actor cambió los nombres de sus campos. Mirá `datos/crudo/` con `cli fuentes inspeccionar --archivo ...` y actualizá el `mapeo` del YAML |

La corrida no se cae por un competidor: sigue con los demás y te lista los
errores al final con nombre y apellido.

### "La IA falló"

No pasa nada grave: el router prueba el siguiente proveedor de la lista y, si
todos fallan, arma el borrador con reglas simples (vas a ver
`stub/stub` como modelo). Es más soso, lo editás más, pero el lunes tenés algo
que mandar. Revisá después `python3 -m pulserival.cli costos`, que muestra los
fallos por proveedor.

### "Se pasó del tope de gasto"

Mensaje: `Gasto de IA en esta corrida: $X (tope $0.75)`. Primero preguntate
por qué: lo normal es ~$0,02 por reporte. Casi siempre es un scraper que
devolvió muchísimos anuncios. Si es legítimo, subí
`tope_gasto_usd_por_corrida` en `config/modelos.yaml`.

### "Mandé el reporte y tenía un error"

No hay deshacer de un email. Lo que sí hay es registro: el reporte queda en
estado `enviado` con fecha, canal y una copia exacta en `salida/`. Para el
siguiente periodo, generá normal; el sistema no deja regenerar ni reenviar uno
ya enviado, justamente para que no se duplique.

### "Se me perdió la base de datos"

`datos/pulserival.db` es un solo archivo: copialo a Drive/Dropbox una vez por
semana, o dejá corriendo el workflow de GitHub Actions, que la versiona en la
rama `datos` en cada corrida. Si se perdió, se pierde el historial de
detección (y con él la capacidad de decir "esto es nuevo"), no los reportes ya
enviados.

---

## Cuándo pasar a Fase 3

Cuando `python3 -m pulserival.cli dataset` devuelva
`"listo_para_fase_3": true` — 8 reportes revisados y una similitud ≥ 0,90 en
los últimos 5. En ese momento: en `pipeline.ciclo_completo()`, envío
automático condicionado a `validacion.aprobado == true` y sin avisos de
cobertura; el resto queda como "spot check".

Sugerencia: aun ahí, dejá manual el primer mes de cada cliente nuevo.
