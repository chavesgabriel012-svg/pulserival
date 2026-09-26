# PulseRival

Reportes recurrentes de inteligencia publicitaria para empresas en Costa Rica.
Cada semana (o cada mes) le dice a un cliente qué anuncios está corriendo su
competencia en Meta y en Google Ads, y qué significa eso para su negocio.

El pipeline es el mismo patrón de Atalalla, aplicado a anuncios de
competidores en vez de publicaciones oficiales:

```
PROCESAR            PRIORIZAR           GENERAR             REVISAR         DISTRIBUIR
scrapers            qué es nuevo,       borrador con IA,    vos editás      email
programados   ──▶   qué cambió,   ──▶   con cita a la ──▶   y queda    ──▶  (o WhatsApp)
(Meta, Google)      qué se cayó         fuente              el diff
```

La recolección es **automática desde el día 1**. El único paso humano es tu
revisión editorial del borrador — y ese paso queda registrado como dato
(borrador, versión final, diff, etiqueta), que es exactamente lo que después
permite reducirlo hasta casi cero.

---

## Probalo en 30 segundos, sin claves y sin costo

```bash
pip install -r requirements.txt
python3 -m pulserival.cli demo
```

La demo crea un cliente piloto ficticio con dos competidores, simula dos
corridas (una semana después de la otra), detecta un cambio de precio y un
anuncio que se cayó, genera el borrador, registra una edición y arma el email.
Abrí el `.html` que deja en `salida/` para verlo como lo recibe el cliente.

## Puesta en marcha real

```bash
make instalar                         # instala las 3 librerías necesarias
cp .env.example .env                  # y llená las claves que vayas usando
make init                             # crea datos/pulserival.db

# 1. tu cliente piloto: lo más simple es escribirlo en config/clientes.yaml
#    y aplicarlo (así queda versionado y el cron lo encuentra solo)
python3 -m pulserival.cli aplicar-config

#    o cargarlo a mano:
python3 -m pulserival.cli clientes agregar \
  --empresa "Nombre S.A." --contacto "Nombre del contacto" \
  --email contacto@cliente.cr --periodicidad semanal \
  --industria "ferreterías" \
  --notas "Contexto que importa: ticket promedio, qué le preocupa, sedes"

# 2. sus competidores (mínimo uno de --meta-pagina / --meta-consulta /
#    --google-dominio; mejor los tres)
python3 -m pulserival.cli competidores agregar --cliente 1 \
  --nombre "Competidor 1" \
  --meta-pagina "https://www.facebook.com/competidor1" \
  --google-dominio "competidor1.co.cr" --prioridad 1

# 3. antes de gastar: probá que ese competidor sí tiene anuncios
python3 -m pulserival.cli prueba-scraper --consulta "Competidor 1" --limite 10

# 4. la corrida (esto es lo que el cron hace solo)
python3 -m pulserival.cli ciclo

# 5. editás el borrador que quedó en borradores/*.md y registrás tu versión
python3 -m pulserival.cli reporte registrar --id 1 \
  --etiqueta tono --razon "Suavicé la conclusión del segundo bloque"

# 6. mirás cómo se ve, y lo mandás
python3 -m pulserival.cli reporte enviar --id 1 --simular   # deja el HTML en salida/
python3 -m pulserival.cli reporte enviar --id 1
```

`python3 -m pulserival.cli --help` lista todos los comandos.

## Cómo está armado

| Carpeta / archivo | Qué hace |
|---|---|
| `pulserival/esquema.sql` | las 8 tablas del sistema, comentadas |
| `pulserival/fuentes/` | de dónde salen los anuncios (Apify, API oficial de Meta, demo) |
| `pulserival/priorizar.py` | detecta qué es nuevo, qué cambió y qué se cayó |
| `pulserival/ia/` | modelos intercambiables + los prompts en archivos `.md` |
| `pulserival/reporte/` | arma el insumo, genera, valida y renderiza el reporte |
| `pulserival/revision/` | tu edición y el diff que alimenta la Fase 2 |
| `pulserival/entrega/` | envío por email (Resend o SMTP) y versión WhatsApp |
| `config/clientes.yaml` | **tus clientes y sus competidores** (se versiona; la base no) |
| `config/modelos.yaml` | **qué modelo de IA usa cada tarea y cuánto cuesta** |
| `config/fuentes.yaml` | **qué scraper se usa y cómo se mapean sus campos** |
| `.github/workflows/` | la recolección programada, sin servidor propio |

Los dos YAML de `config/` son a propósito el lugar donde vas a tocar cosas:
cambiar de modelo o arreglar un campo de un scraper no requiere tocar código.

## Documentación

| Documento | Para qué |
|---|---|
| [00 · Visión y fases](docs/00-vision-y-fases.md) | qué se automatiza en cada fase y cómo se mide el salto |
| [01 · Decisiones técnicas](docs/01-decisiones-tecnicas.md) | el stack, explicado sin jerga, y qué se descartó |
| [02 · Fuentes de datos](docs/02-fuentes-de-datos.md) | por qué scraper y no API, costos reales, límites legales |
| [03 · API oficial de Meta, paso a paso](docs/03-meta-api-paso-a-paso.md) | cómo sacar el token, y para qué sirve de verdad |
| [04 · Modelos de IA](docs/04-modelos-ia.md) | qué modelo para qué tarea, con tus $9 de Gemini y Groq gratis |
| [05 · Qué necesito de vos](docs/05-que-necesito-de-vos.md) | la lista concreta antes de la primera corrida real |
| [06 · Operación semanal](docs/06-operacion-semanal.md) | tu rutina de 20 minutos y qué hacer cuando algo falla |
| [07 · Presupuesto](docs/07-presupuesto.md) | qué se paga y qué no, con precios verificados, y el dominio |
| [08 · Cobro y lanzamiento](docs/08-cobro-y-lanzamiento.md) | los planes, cómo se cobra hoy y qué falta probar con una transacción real |
| [09 · Deploy](docs/09-deploy.md) | cómo se publica la web en Vercel, y por qué el alta va a un YAML y no a la base |

## Pruebas

```bash
make prueba     # 108 tests, sin red, en un segundo
```

Cubren lo que más duele si se rompe: la detección de cambios, la clasificación
del periodo, el control de calidad del borrador, el registro del diff, el
mapeo de los scrapers y los seguros de envío (nunca se manda un borrador sin
revisar, nunca se manda dos veces).

`tests/test_regresiones.py` tiene un test por cada bug que encontró la revisión
de código, con el comentario de qué pasaba antes. Si alguno vuelve, falla.
