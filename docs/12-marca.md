# La identidad visual

Todo lo que el cliente ve —la web, el panel y el correo del reporte— usa la
misma identidad. Vive en `pulserival/marca/` y no se copia a mano a ninguna
plantilla: si un color o el logo cambian, se cambian ahí y las tres caras
del producto se mueven juntas.

## Qué hay

```
pulserival/marca/
  __init__.py          los tokens de color y las ayudas (PALETA, fuera_de_paleta, data_uri)
  activos/
    logo.svg           el logo sobre fondo claro
    logo-negativo.svg  el mismo, sobre fondo oscuro (derivado del anterior)
    favicon.svg .ico   el icono de la pestaña
    favicon-16/32/48.png, apple-touch-icon.png, icon-192.png, icon-512.png
    logo-correo.png    el logo del correo, en PNG porque Gmail no rinde SVG
    og-image.png       la imagen que sale al compartir un enlace
    site.webmanifest
```

Los PNG se generaron rasterizando los SVG, no dibujando aparte: así no pueden
quedar diciendo una cosa distinta del vector.

`logo-negativo.svg` se deriva de `logo.svg` cambiando `#141312` por `#F4F2EE`
y `#DD4115` por `#EF5B36`. Si llega un logo nuevo, se reemplaza `logo.svg` y
se vuelven a derivar los demás; reemplazar solo uno deja los dos distintos y
nadie lo nota hasta que el cliente abre el correo.

## La regla de color

Papel y tinta hacen el 95 % del trabajo. El naranja Pulso aparece poco —el
ojo del logo, el botón principal, las marcas de cambio del reporte— y nunca
en degradado.

| Token | Hex | Para qué |
|---|---|---|
| `TINTA` | `#141312` | texto principal, fondos oscuros |
| `PAPEL` | `#F4F2EE` | fondo principal |
| `BLANCO` | `#FFFFFF` | tarjetas sobre papel |
| `PULSO` | `#DD4115` | acento sobre claro |
| `PULSO_OSCURO` | `#EF5B36` | acento sobre fondo oscuro |
| `PULSO_TEXTO` | `#B22800` | acento en texto chico sobre claro (contraste AA) |
| `PIEDRA` | `#8A857D` | bordes fuertes; nunca texto chico |
| `GRIS_TEXTO` | `#6B665E` | texto secundario sobre papel |
| `LINEA` | `#D9D5CE` | divisores sobre papel |
| `ESCRITORIO` | `#1E1D1B` | fondo oscuro de secciones |
| `LINEA_OSCURA` | `#3A3834` | divisores sobre fondo oscuro |
| `TEXTO_OSCURO_2` | `#B5B0A6` | texto secundario sobre fondo oscuro |

Las tipografías son Schibsted Grotesk (títulos), Source Serif 4 (cuerpo) e
IBM Plex Mono (rótulos). En el correo no se cargan: ningún cliente de correo
garantiza fuentes web, así que ahí se usan las pilas del sistema. Es a
propósito y no hace falta arreglarlo.

## Qué lo mantiene parejo

`marca.fuera_de_paleta(texto)` devuelve los colores de un HTML que no están
en la paleta. Lo corren los tests de las dos páginas públicas
(`test_landing.py`), de las cuatro pantallas del panel (`test_panel.py`) y
del correo (`test_render.py`), así que un hexadecimal suelto en una plantilla
hace fallar `make prueba` en vez de llegar al cliente.

El color nunca es el único dato: en el correo, lo nuevo, lo que cambió y lo
que se apagó se distinguen además por símbolo (`+`, `Δ`, `−`), porque una
parte de la gente no ve la diferencia entre el naranja y el gris.

## Cómo se sirven

- **La web servida por Flask** los pide a `/marca/<archivo>`, con caché de un
  año (los archivos no cambian de nombre, así que si cambia el logo hay que
  esperar a que el navegador suelte la caché o renombrarlo).
- **La landing estática** (`cli landing`) copia `activos/` al lado del HTML,
  para que la carpeta funcione abierta con doble clic.
- **El correo** manda el logo como data URI. Cada KB cuenta: Gmail recorta
  el correo pasando los 102.400 bytes, y el recorte automático de
  `render.email_html` existe por eso.
