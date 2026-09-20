# 01 · Decisiones técnicas, explicadas sin jerga

El criterio de todas las decisiones de acá fue el mismo: **que vos puedas
operar y arreglar esto solo, sin equipo técnico**. Cuando había que elegir
entre "más potente" y "menos cosas que se pueden romper", gana lo segundo.

## Python + SQLite, un solo archivo de base de datos

**Qué es SQLite:** una base de datos que vive en un archivo
(`datos/pulserival.db`). No hay servidor que se caiga, no hay contraseña de
base de datos, no hay costo mensual. Lo copiás a Drive y ese es tu respaldo.
Lo abrís con [DB Browser for SQLite](https://sqlitebrowser.org/) (gratis) y
ves tus datos en una tabla, como en Excel.

**Por qué no Postgres/Supabase/Airtable:** porque para 5, 20 o 100 clientes
con reportes semanales, SQLite va sobrada — estamos hablando de miles de
filas, no millones. Un Postgres administrado agrega una cuenta más, una
factura más y una cosa más que puede fallar a las 5 a.m. Si algún día tenés
varias personas escribiendo al mismo tiempo desde distintas máquinas, ahí sí
conviene mover a Postgres: el código está escrito con SQL estándar, así que
es un cambio acotado a `pulserival/db.py`.

**Por qué Python:** los scrapers, las APIs de IA y el manejo de texto son su
terreno natural, y es el lenguaje con más ejemplos y más ayuda disponible
cuando te trabés.

## Solo dos librerías externas

`requirements.txt` tiene dos líneas: `Jinja2` (plantillas del email) y
`requests` (llamadas HTTP). Todo lo demás es la biblioteca estándar de
Python, incluido el lector de `.env`, el diff y el conversor de Markdown.

Cada librería que se instala es algo que en seis meses se actualiza, cambia
de comportamiento y rompe la corrida del lunes. Dos es un número que podés
mantener sin pensar.

## Línea de comandos, no interfaz web

No hay panel de administración. Hay comandos: `clientes agregar`,
`recolectar`, `reporte enviar`. Construir una interfaz web serían semanas de
trabajo y un servidor más que mantener, para reemplazar algo que ya funciona
con una línea de texto.

Cuando el volumen lo justifique (digamos, más de 15 clientes o si alguien más
opera el sistema), lo natural es agregar una interfaz mínima sobre la misma
base de datos, sin tocar el pipeline.

## Programación: GitHub Actions, sin servidor

`.github/workflows/recoleccion.yml` corre todos los lunes a las 5 a.m. de
Costa Rica en las máquinas de GitHub. No pagás servidor, no dejás tu laptop
prendida. La base actualizada queda como archivo descargable y versionada en
una rama `datos`, así tenés historial de cada corrida.

La alternativa es `scripts/cron-local.sh` con el cron de tu propia máquina.
Más simple todavía, pero depende de que la máquina esté encendida.

## El borrador se edita en un archivo de texto

No hay editor especial. `reporte exportar` deja un `.md` en `borradores/`
con el borrador y, arriba, el resultado del control de calidad. Lo abrís con
cualquier editor, lo cambiás y corrés `reporte registrar`. El sistema calcula
el diff solo.

Por qué así: porque ya editás texto todos los días, y porque un archivo de
texto se puede versionar, comparar y guardar para siempre. El diff es el
activo del proyecto (ver [Fase 2](00-vision-y-fases.md)), y así sale gratis.

## Todo lo caro o cambiante está en YAML, no en el código

| Si cambia... | Tocás... |
|---|---|
| el precio o el nombre de un modelo de IA | `config/modelos.yaml` |
| el actor de Apify o un campo de su salida | `config/fuentes.yaml` |
| el tono o las reglas del reporte | `pulserival/ia/prompts/*.md` |
| tu tope de gasto por corrida | `config/modelos.yaml` |

Ninguno de esos cambios necesita entender Python. Eso es deliberado: los
precios de los modelos cambiaron tres veces en el último año, y los scrapers
cambian campos sin avisar.

## Las cosas que el sistema se niega a hacer

Están en el código porque un error acá cuesta un cliente:

- No envía un reporte que todavía está en estado `borrador` (salvo `--forzar`).
- No envía el mismo reporte dos veces.
- No regenera un reporte que ya se envió.
- No gasta más del tope de IA por corrida (`tope_gasto_usd_por_corrida`).
- Si no hay claves de IA configuradas, genera el borrador con reglas simples
  en vez de fallar: siempre tenés algo que revisar.
- Si un scraper falla para un competidor, sigue con los demás y te lista el
  error al final, con el nombre del competidor.
