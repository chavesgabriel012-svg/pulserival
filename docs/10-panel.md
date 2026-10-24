# 10 · El panel de revisión

## Para qué existe

El producto tiene un paso humano y es el que lo hace defendible: **ningún
reporte le llega a un cliente sin que usted lo lea y lo apruebe**. Hasta ahora
ese paso era por CLI: `reporte exportar` dejaba un `.md`, usted lo editaba en un
editor, `reporte registrar` guardaba su versión con el diff, y `reporte enviar`
lo mandaba.

El panel es la misma secuencia en el navegador. **Llama a las mismas funciones**:
el diff sigue yendo a `ediciones_registradas`, que es el activo que después
permite ajustar los prompts con evidencia en vez de con intuición. Revisar por
el panel no pierde nada de lo que se gana revisando el `.md`. El CLI sigue
funcionando; son dos puertas a lo mismo.

## Lo que el panel no hace, a propósito

- **No da de alta, no cobra ni da de baja.** Eso sigue siendo `cli clientes`
  y Paddle.
- **No gasta sin que usted lo confirme.** Buscar competidores, recolectar y
  rehacer un reporte se pueden disparar desde la página de cada cliente, pero
  cada botón dice qué consume y pide marcar una casilla. Un botón que gasta
  es un botón que se aprieta sin pensar.

## Clientes y competidores

El reporte sale aunque el cliente no indique competidores: el ciclo los
deduce de su industria (ver `docs/08`, sección 5.6). Eso deja casos que
tiene que resolver una persona, y la bandeja los avisa arriba de todo:

- **Sin competidores:** la búsqueda no encontró a nadie (industria "Otra",
  un nombre que no dice el rubro, Apify caído).
- **Elegidos por el sistema:** competidores que dedujo la búsqueda y nadie
  revisó. Se confirman guardándolos sin cambios.
- **Sin anuncios:** un competidor del que, después de una corrida, no llegó
  ningún anuncio. Puede que no paute, o que el nombre o la página estén mal.

En **Clientes** (menú de arriba) aparecen primero los que tienen algo que
resolver. La página de cada cliente permite:

- **Agregar** un competidor con solo el nombre; la página de Facebook y el
  sitio web son opcionales y afinan la búsqueda.
- **Corregir** uno (nombre, Facebook, sitio, prioridad) o **quitarlo**. Quitar
  lo desactiva sin borrar sus anuncios; si se vuelve a agregar con el mismo
  nombre, recupera su historial.
- **Corregir el correo, la industria y el contexto** del cliente.
- **Buscar competidores por su industria** (Apify: hasta 40 anuncios por
  término). No repite a los que ya tiene ni a los que se quitaron.
- **Recolectar y generar el reporte** para ese cliente, sin esperar al ciclo
  semanal (Apify e IA). Si no tiene competidores, primero los busca.
- **Rehacer el reporte con lo que ya hay** (solo IA), después de corregir
  competidores o contexto. Reemplaza el borrador: lo editado a mano en ese
  reporte se pierde.

Las tres últimas corren en segundo plano y la página se actualiza sola hasta
que terminan. Una a la vez, y nunca junto con el ciclo programado: si hay
otra corrida en curso, el botón espera. El aviso de cómo terminó vive en la
memoria del servidor: un reinicio lo borra, pero no lo que la tarea ya guardó.

La página del reporte también avisa si los competidores de ese cliente piden
revisión, con un enlace a su ficha.

## Entrar

El panel está en `/panel/`. Hace falta `PULSERIVAL_PANEL_CLAVE`.

**Sin esa variable el panel no se sirve**: devuelve 503 y dice qué falta. No
queda abierto. Un panel accesible en internet con un botón de "enviar al
cliente" es peor que no tener panel.

```
PULSERIVAL_PANEL_CLAVE=una-contraseña-larga-y-propia
PULSERIVAL_SECRET=otra-cadena-al-azar-larga     # firma la cookie de sesión
PULSERIVAL_HTTPS=1                              # solo cuando esté publicado
```

`PULSERIVAL_SECRET` no es opcional en la práctica: si falta, se genera una al
azar en cada arranque y la sesión se cae en cada reinicio. Lo que no se hace es
dejar una clave fija escrita en el código, porque cualquiera que lea el
repositorio podría firmarse una sesión de administrador.

`PULSERIVAL_HTTPS=1` hace que la cookie viaje solo por HTTPS. En local no se
pone, porque en local no hay HTTPS y la sesión no funcionaría.

## El recorrido

### 1. Bandeja

Los reportes en estado `borrador` o `revisado`, con lo que hace falta para
decidir sin abrirlos: cliente, periodo, cuántos movimientos trae, y si el
control de calidad lo aprobó o lo marcó para revisar, con cuántos problemas y
avisos. Abajo, los últimos veinticinco ya decididos.

### 2. Abrir un reporte

Arriba, lo que hay que mirar antes de aprobar, sin plegar:

- **Los problemas del control de calidad.** No bloquean: la decisión es suya.
  Pero cada punto es algo que el cliente podría notar.
- **Los datos dudosos de la corrida.** Un competidor que devolvió cero anuncios
  puede no estar pautando, o puede que no lo estemos encontrando. Si el reporte
  dice que hay un hueco ahí, confírmelo a mano. Ya pasó una vez, con Artelec.

Abajo, a la izquierda el texto editable; a la derecha las acciones y el insumo:
los movimientos por clasificación y los anuncios agrupados por competidor, cada
uno con su enlace a la ficha pública. Ese enlace es lo que hace verificable el
reporte.

Plegados, tres vistas: el borrador renderizado, **el correo tal como le va a
llegar al cliente** (armado con el mismo código que lo manda, no con otro), y el
borrador original de la IA si ya lo editó.

### 3. Guardar su versión

Guardar es lo que deja constancia de que usted lo revisó, y pasa el reporte a
`revisado`. **Hasta que no guarde, el envío está bloqueado.** Aunque no le
cambie nada: guardar sin cambios queda registrado como `sin_cambios`, que
también es un dato útil.

El formulario pide qué tipo de cambio hizo y por qué, en una línea. No es
burocracia: el diff con su etiqueta y su razón es lo que después permite
corregir los prompts. Si deja la etiqueta vacía la deduce una IA, y eso cuesta
una llamada.

### 4. Aprobar y enviar

Para confirmar el envío hay que **escribir el nombre del cliente**. Es un clic
de más contra un error que no se deshace: el correo ya salió. Al lado está
"simular el envío", que arma el correo y guarda la copia sin mandar nada.

### 5. Descartar

Pide el motivo y no lo hace opcional. Un reporte descartado sin explicación es
una señal perdida: es la forma más directa de saber qué produce borradores
inservibles.

Un reporte ya enviado no se puede descartar ni volver a enviar. Lo que salió ya
salió.

## Gasto

`/panel/gasto` separa dos cosas que no conviene mezclar: la **recolección** se
paga por anuncio traído, el **análisis** se paga por token. Sumarlas en un solo
número esconde cuál se fue de precio.

Muestra el acumulado de cada una, el desglose de IA por tarea y modelo con sus
fallos —una llamada que falla y cae al modelo siguiente se cobra igual—, el
costo de IA por cliente, y las últimas quince corridas.

La recolección no se puede repartir por cliente: una corrida trae los anuncios
de todos juntos.

## Los seguros

Cuatro cosas son ciertas siempre, y hay un test por cada una:

1. Sin `PULSERIVAL_PANEL_CLAVE`, ninguna ruta del panel responde.
2. Sin sesión, no se llega a nada.
3. Un POST sin el token de la sesión no pasa. Sin eso, una página cualquiera
   podría hacer POST a `/enviar` usando la cookie de su navegador y mandarle el
   reporte al cliente.
4. No se envía un reporte que no pasó por su revisión, ni se envía dos veces.

Además: la contraseña se compara en tiempo constante, hay tope de intentos por
IP, el login no redirige a un dominio ajeno, y las páginas del panel piden a los
buscadores que no las indexen.

## Lo que falta

- **No hay usuarios, hay una contraseña.** Con una sola persona operando
  alcanza. Cuando haya dos, hace falta saber quién aprobó qué.
- **La sesión dura doce horas** y no hay forma de cerrarla desde otro
  dispositivo más que cambiando `PULSERIVAL_SECRET`.
- **El tope de intentos está en memoria del proceso.** Con un solo proceso
  (`--workers 1`, que es la configuración) funciona.
- **El panel no muestra el estado del cron.** Por ahora se ve en `/salud`, que
  dice cuándo fue la última corrida y por qué la siguiente todavía no salió.
