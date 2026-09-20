# 03 · API oficial de Meta Ad Library, paso a paso

**Antes de empezar, lo importante:** esta API **no** reemplaza al scraper para
tu caso de uso principal. Para anuncios comerciales dirigidos a Costa Rica
devuelve vacío, por decisión de plataforma. Ver
[02 · Fuentes de datos](02-fuentes-de-datos.md).

Sirve para: competidores que también pautan en UE/UK, y clientes que necesitan
anuncios políticos o de temas sociales. Hacé este trámite porque es gratis,
tarda poco y te abre esos dos casos — no porque vaya a resolver el principal.

## Paso 1 · Cuenta de desarrollador

1. Entrá a <https://developers.facebook.com/> con tu cuenta personal de
   Facebook (tiene que ser una cuenta real y verificada).
2. Arriba a la derecha: **Iniciar sesión** → **Comenzar**.
3. Aceptá los términos de plataforma y confirmá tu correo.

## Paso 2 · Verificación de identidad

Este es el paso que tarda: Meta exige confirmar quién sos y dónde estás para
dar acceso a la Ad Library API.

1. Andá a <https://www.facebook.com/id> (Confirmación de identidad).
2. Elegí el país: **Costa Rica**.
3. Subí un documento oficial (cédula o pasaporte). Foto nítida, las cuatro
   esquinas visibles, sin reflejos.
4. Esperá la aprobación. Suele ser de horas a unos pocos días.

> Si el trámite se rechaza, casi siempre es la calidad de la foto o que el
> nombre del documento no coincide con el del perfil. Corregilo y reintentá.

## Paso 3 · Crear la app

1. En <https://developers.facebook.com/apps/> → **Crear app**.
2. Caso de uso: elegí **Otro** y después el tipo **Empresa**.
3. Nombre: `PulseRival` (o el que quieras; es interno).
4. No hace falta agregar ningún producto ni configurar permisos especiales:
   la Ad Library API no usa el flujo normal de permisos de la Graph API.
5. Guardá el **ID de la app**.

## Paso 4 · Aceptar los términos de la Ad Library API

1. Entrá a <https://www.facebook.com/ads/library/api/>.
2. Seguí el enlace para solicitar acceso y aceptá los términos de uso de los
   datos. Acá es donde se valida que tu identidad ya esté confirmada.

## Paso 5 · Generar el token

1. Abrí el **Explorador de la API Graph**:
   <https://developers.facebook.com/tools/explorer/>
2. Arriba a la derecha, seleccioná tu app en **Aplicación de Meta**.
3. En **Token de usuario**, hacé clic en **Generar token de acceso**.
4. Copiá el token y pegalo en tu `.env`:

```env
META_AD_LIBRARY_TOKEN=EAAG...
```

> El token del explorador es de **corta duración** (unas horas). Para uso
> programado, cambialo por uno de larga duración en la
> **Herramienta de depuración de tokens de acceso**
> (<https://developers.facebook.com/tools/debug/accesstoken/>) →
> **Extender el token de acceso**. Aun así, caduca: anotá en tu calendario
> revisarlo cada dos meses. Si vence, la fuente falla con un mensaje claro y
> el resto de la corrida sigue funcionando.

## Paso 6 · Probar que funciona

Esta consulta busca anuncios en Costa Rica. Va a devolver `data: []`, y eso
es lo esperado: confirma que el token sirve y que la restricción es de la
plataforma, no tuya.

```bash
curl -G "https://graph.facebook.com/v21.0/ads_archive" \
  --data-urlencode "access_token=$META_AD_LIBRARY_TOKEN" \
  --data-urlencode "search_terms=gimnasio" \
  --data-urlencode 'ad_reached_countries=["CR"]' \
  --data-urlencode "ad_type=ALL" \
  --data-urlencode "fields=id,page_name,ad_creative_bodies,ad_delivery_start_time" \
  --data-urlencode "limit=5"
```

Para comprobar que el token **sí** trae datos donde la API los tiene, repetilo
cambiando el país a uno de la UE:

```bash
  --data-urlencode 'ad_reached_countries=["ES"]'
```

Ahí deberías ver anuncios. Esa es exactamente la diferencia que documenta
[02 · Fuentes de datos](02-fuentes-de-datos.md).

## Paso 7 · Activarla en PulseRival

En `config/fuentes.yaml`:

```yaml
meta_api_oficial:
  activa: true
  paises_por_defecto: ["CR", "ES"]   # agregá los países que apliquen
```

La fuente se usa cuando la pedís explícitamente y **nunca** reemplaza al
scraper en la corrida normal: son fuentes complementarias, y cada anuncio
guardado deja registrado de cuál vino (columna `fuente`).

## Errores comunes

| Mensaje | Qué pasa |
|---|---|
| `(#10) Application does not have permission for this action` | falta aceptar los términos del Paso 4, o la identidad no está confirmada |
| `Invalid OAuth access token` | el token caducó: regeneralo (Paso 5) |
| `data: []` con país CR | **es lo normal.** No es un error: ver arriba |
| `(#613) Calls to this api have exceeded the rate limit` | demasiadas consultas seguidas; esperá y bajá el `limit` |
