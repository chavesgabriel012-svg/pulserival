# 02 · Fuentes de datos: qué se puede y qué no

Esta es la parte donde la mayoría de los proyectos parecidos se estrellan.
Resumen en una línea: **para anuncios comerciales dirigidos a Costa Rica no
existe API oficial, ni en Meta ni en Google.**

## Meta

### La API oficial no sirve para el caso de uso principal

El endpoint `ads_archive` de la Graph API solo devuelve:

1. anuncios de **temas políticos o sociales**, en cualquier país; y
2. **cualquier** anuncio, comercial incluido, que se haya entregado en la
   **Unión Europea o Reino Unido** (obligación del DSA).

Un comercio tico que pauta solo para Costa Rica no cae en ninguna de las dos.
La API devuelve una lista vacía, y **eso no se arregla con un token mejor**:
no es un problema de permisos, es una restricción de la plataforma.

Vale la pena tenerla igual, y está implementada
(`pulserival/fuentes/meta_api.py`), para dos casos reales:

- un cliente cuyos competidores también pautan en UE/UK;
- un cliente que necesita anuncios políticos o de interés social (partidos,
  cámaras, ONGs, campañas de temas sociales) — que en año electoral en Costa
  Rica es un producto vendible por sí solo.

El paso a paso para sacar el token está en
[03 · API oficial de Meta](03-meta-api-paso-a-paso.md).

### La interfaz web pública sí muestra todo

`facebook.com/ads/library` muestra cualquier anuncio activo, de cualquier país
y categoría, sin esa restricción. Lo que no tiene es una API de acceso masivo.

### Fuente primaria: scraper ya construido, llamado por API

Usamos el actor de Apify [`apify/facebook-ads-scraper`](https://apify.com/apify/facebook-ads-scraper),
que raspa esa interfaz pública y se puede llamar por API y programar.

**Por qué un scraper de terceros y no uno propio:** un scraper propio se
rompe cada vez que Meta cambia el HTML — o sea, seguido. Arreglarlo es trabajo
de ingeniería continuo, justo lo que no querés tener. Apify mantiene el actor
y cobra por resultado.

**Costo:** el actor cobra por anuncio devuelto (del orden de unos pocos
dólares por 1.000 anuncios, según el plan). Con 3 competidores × 40 anuncios
× 4 corridas al mes ≈ 480 anuncios por cliente por mes: centavos por reporte.
El número exacto verificalo en la página del actor antes de facturar, porque
cambia.

## Google

El [Centro de Transparencia de Anuncios](https://adstransparency.google.com/)
tampoco tiene API oficial pública. Mismo enfoque: el actor
[`pulsedata/google-ads-transparency-scraper`](https://apify.com/pulsedata/google-ads-transparency-scraper),
que acepta dominios o nombres de anunciante como consulta y filtra por región
(usamos `CR`), y cobra por anuncio devuelto — más barato todavía que el de Meta.

## Las dos entran por la misma puerta

Meta y Google devuelven campos distintos, con nombres distintos. El diseño
resuelve eso en dos piezas:

1. **`AnuncioCrudo`** (`pulserival/fuentes/base.py`): el modelo interno único.
   Todo el resto del sistema solo conoce este formato. Agregar TikTok o
   LinkedIn mañana es escribir una fuente nueva sin tocar nada más.
2. **El mapeo en `config/fuentes.yaml`**: por cada campo interno, la lista de
   nombres posibles en la salida del scraper, probados en orden.

```yaml
mapeo:
  texto: [adText, body, ad_creative_body, snapshot.body.text, text]
```

Si mañana el actor renombra `adText` a `ad_text`, agregás el nombre a la
lista y listo. **No se toca código.**

Para descubrir los nombres reales: cada corrida guarda la respuesta cruda en
`datos/crudo/`, y este comando te la resume:

```bash
python3 -m pulserival.cli fuentes inspeccionar --archivo datos/crudo/meta-1-20260920-050000.json
```

## Lo que hay que tener claro antes de vender

- **Los datos que existen son:** el texto del anuncio, el creativo (imagen o
  video), el link de destino, la fecha de inicio informada por la plataforma,
  el formato y la plataforma de publicación.
- **Los datos que NO existen:** inversión, presupuesto, alcance, impresiones,
  clics, conversiones, resultados. No los tiene nadie fuera del anunciante.
  Decírselo al cliente de entrada es una ventaja comercial: te diferencia de
  quien promete métricas que no puede tener. El pie de cada email ya lo
  aclara, y el validador rechaza el borrador si el modelo las inventa.
- **Cobertura:** solo se ve lo que está activo o archivado en la biblioteca
  pública al momento de la corrida. Un anuncio que corrió 3 días entre dos
  corridas semanales puede no aparecer nunca. Con cadencia semanal esa
  ventana es aceptable; con cadencia mensual, es un punto ciego que conviene
  mencionarle al cliente.
- **Nada de esto requiere acceso a las cuentas publicitarias del competidor.**
  Todo sale de bibliotecas que las plataformas publican por obligación legal
  o por política propia. Aun así: no rasparlo desde tu propia IP, no
  redistribuir los creativos como si fueran tuyos, y citar la fuente en cada
  reporte (el anexo del email linkea cada anuncio a su ficha pública).
