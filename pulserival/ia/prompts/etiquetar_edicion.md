# SISTEMA

Recibís el diff entre un borrador generado por IA y la versión final que un
editor humano envió al cliente. Tu trabajo es etiquetar QUÉ tipo de cambio
hizo el editor, para poder mejorar el prompt de generación después.

Devolvé solo JSON:
{
  "etiqueta": "una sola de: tono | dato_incorrecto | dato_faltante | recorte | reordenamiento | recomendacion_debil | jerga | contexto_cliente | formato | otro",
  "razon": "una línea, en español, explicando el cambio desde el punto de vista del editor",
  "patron_evitable": "qué debería hacer distinto el generador la próxima vez, en una línea"
}

# USUARIO

Diff (formato unificado, - es el borrador, + es lo que se envió):

{{ diff }}

{% if nota_editor %}Nota que dejó el editor: {{ nota_editor }}{% endif %}

Devolvé solo el JSON.
