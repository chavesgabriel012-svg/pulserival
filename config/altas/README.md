# Altas recibidas por el formulario

Cada archivo `.yaml` de esta carpeta es **una solicitud de alta** que llegó del
formulario de la landing. Las escribe el servidor web, no una persona: los
datos son los que cargó quien llenó el formulario y **no están verificados**.

Existe esta carpeta, en vez de escribir directo en `../clientes.yaml`, por dos
razones concretas:

1. reescribir `clientes.yaml` le borraría los comentarios, que son la
   documentación de cada competidor (por qué Artelec lleva `meta_pagina_id`,
   por qué el dominio de SIMAN es `siman.com`);
2. dos altas al mismo tiempo se pisarían. Un archivo por alta no tiene
   conflicto posible.

## Ninguna de estas altas gasta plata

Todas se depositan con `activo: false` y `estado_suscripcion: pendiente_pago`.
Con cualquiera de las dos, el ciclo las saltea: no se llama al scraper ni a la
IA. `aplicar-config` las carga a la base, pero inactivas.

## Cómo se activa una

1. Confirmar el pago.
2. Revisar los competidores. `python3 -m pulserival.cli prueba-scraper ...`
   dice si de verdad hay anuncios para esa página o ese dominio; es más barato
   descubrir acá que el dato está mal que en la primera corrida.
3. Mover la entrada a `../clientes.yaml` —donde se le pueden agregar los
   comentarios y el contexto del negocio— y borrar el archivo de acá. O, si
   corre, dejarlo acá y cambiarle `activo: true` y
   `estado_suscripcion: activa`.
4. `python3 -m pulserival.cli aplicar-config`

## Si una clave está repetida

`clientes.yaml` gana siempre. Un alta cuya clave ya existe se saltea y
`aplicar-config` lo avisa con `AVISO ·`. Nunca sobreescribe un cliente que ya
está cargado: es una bandeja de entrada pública y no puede cambiarle los datos
a alguien que ya paga.
