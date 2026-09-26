# 09 · Dónde y cómo se publica

## La pregunta corta: ¿Vercel?

**La landing sola, sí. Todo lo demás, no.** Dos razones, las dos verificadas:

1. **SQLite no persiste en Vercel.** Las funciones son efímeras: el único
   directorio escribible es `/tmp` y se borra entre invocaciones. Cada
   instancia tiene su propio sistema de archivos, así que dos peticiones
   seguidas pueden ver bases distintas. Un alta se guardaría y desaparecería.
2. **El plan Hobby prohíbe el uso comercial.** Vercel define uso comercial
   como cualquier despliegue del que alguien obtenga beneficio económico, e
   incluye explícitamente "procesar pagos". Cobrar suscripciones entra de
   lleno: serían **$20/mes** del plan Pro.

Ese segundo punto da vuelta la comparación: Vercel deja de ser el barato.

| | Railway | Vercel Pro | Fly.io |
|---|---|---|---|
| Costo base | ~$5/mes | $20/mes | ~$5/mes |
| Disco persistente | sí (~$0.15/GB) | no | sí |
| SQLite funciona | sí | **no** | sí |
| Uso comercial en el plan base | sí | requiere Pro | sí |

**Recomendación: Railway.** Un solo servicio que sirve la web y guarda la
base en un disco persistente.

Si de todas formas quiere usar Vercel, la combinación que funciona es:
landing estática en Vercel (`cli landing` genera el HTML) y el backend en
otro lado. Son dos lugares que mantener en vez de uno; no lo recomiendo
mientras el volumen sea bajo.

## Lo que hay que configurar

Variables de entorno en el servicio:

```
PULSERIVAL_DB=/datos/pulserival.db     # dentro del disco persistente
PULSERIVAL_COBRO=simulado              # 'real' cuando haya pasarela
PULSERIVAL_VERIFICAR_ALTA=0            # 1 para verificar al dar de alta
APIFY_TOKEN=...
GEMINI_API_KEY=...
GROQ_API_KEY=...
RESEND_API_KEY=...                     # o SMTP_*
```

El `Procfile` ya está:

```
web: gunicorn 'pulserival.web.app:wsgi()' --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

**`--workers 1` es a propósito.** SQLite aguanta muchos lectores pero un solo
escritor. Con este volumen un proceso sobra, y evita que dos escrituras
simultáneas se peleen por el archivo. Cuando haga falta más, el paso
siguiente es Postgres, no más workers.

## Probarlo local antes de publicar

```bash
python3 -m pulserival.cli servidor
```

Levanta en `http://127.0.0.1:5000`. Avisa que el cobro está simulado.

## El cron, después de publicar

Hoy corre en GitHub Actions contra la base versionada en la rama `datos`.
Cuando la base se mude al disco del servidor, **las dos no pueden convivir**:
serían dos bases distintas divergiendo en silencio, y el cliente que se dio
de alta por la web no existiría para el cron.

Al migrar hay que hacer las dos cosas, no una:

1. Copiar la base actual (rama `datos`) al disco persistente, una sola vez.
2. Apagar el `schedule:` de `.github/workflows/recoleccion.yml` y correr el
   ciclo desde el servidor.

Para el paso 2 la opción más simple es la tarea programada del propio
hosting (Railway: "Cron Schedule" en el servicio) corriendo exactamente el
mismo comando de siempre:

```
python -m pulserival.cli ciclo --modo auto --limite 50
```

No hay lógica nueva: es el mismo `pipeline.ciclo_completo()` que corre hoy.

## Qué NO está hecho todavía

- **El webhook de la pasarela.** Hoy el cobro es simulado. Con
  `PULSERIVAL_COBRO=real`, el checkout manda al enlace de pago y la
  confirmación por navegador queda bloqueada (403), pero todavía no existe
  el endpoint que recibe la notificación de la pasarela y activa solo. Hasta
  que exista, se activa a mano: `cli clientes activar --id N --referencia ...`
- **El panel de administración.** La revisión y el envío siguen por CLI.
- Ver `docs/08-cobro-y-lanzamiento.md` para lo que falta probar con una
  transacción real.
