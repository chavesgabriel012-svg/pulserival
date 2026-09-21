-- ════════════════════════════════════════════════════════════════════
--  PulseRival — esquema de datos (SQLite)
--  Un solo archivo .db. Se puede abrir con cualquier visor de SQLite
--  (DB Browser for SQLite, gratis) si querés mirar los datos a mano.
-- ════════════════════════════════════════════════════════════════════

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── 1. CLIENTES ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clientes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    clave             TEXT    UNIQUE,      -- id estable para config/clientes.yaml
    nombre_empresa    TEXT    NOT NULL,
    contacto_nombre   TEXT,
    contacto_email    TEXT    NOT NULL,
    contacto_whatsapp TEXT,
    periodicidad      TEXT    NOT NULL DEFAULT 'semanal'
                              CHECK (periodicidad IN ('semanal','mensual')),
    dia_envio         TEXT    DEFAULT 'martes',   -- día preferido de entrega
    industria         TEXT,
    notas             TEXT,                       -- contexto que mejora el reporte
    activo            INTEGER NOT NULL DEFAULT 1,
    creado_en         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── 2. COMPETIDORES SEGUIDOS ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competidores_seguidos (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id             INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    nombre                 TEXT    NOT NULL,      -- cómo lo llamamos en el reporte
    clave                  TEXT,                  -- id estable dentro del cliente
    -- Meta: cualquiera de los dos sirve. La página es más precisa.
    meta_pagina_url        TEXT,                  -- https://www.facebook.com/nombre
    meta_pagina_id         TEXT,
    meta_consulta          TEXT,                  -- término de búsqueda alternativo
    -- Google: el dominio es lo más confiable para el Centro de Transparencia.
    google_dominio         TEXT,                  -- ejemplo.co.cr
    google_anunciante      TEXT,
    google_anunciante_id   TEXT,                  -- AR01234... si ya lo conocés
    prioridad              INTEGER NOT NULL DEFAULT 2,  -- 1 = el que más importa
    notas                  TEXT,
    activo                 INTEGER NOT NULL DEFAULT 1,
    creado_en              TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_competidores_cliente ON competidores_seguidos(cliente_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_competidores_clave
    ON competidores_seguidos(cliente_id, clave) WHERE clave IS NOT NULL;

-- ── 3. CORRIDAS DE RECOLECCIÓN (trazabilidad de cada ejecución) ──────
CREATE TABLE IF NOT EXISTS corridas_recoleccion (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciada_en    TEXT    NOT NULL DEFAULT (datetime('now')),
    terminada_en   TEXT,
    estado         TEXT    NOT NULL DEFAULT 'en_curso'
                           CHECK (estado IN ('en_curso','ok','parcial','error')),
    fuente         TEXT,                          -- apify | fixtures | meta_api
    disparada_por  TEXT,                          -- cron | manual
    resumen_json   TEXT,                          -- conteos, errores, costo
    costo_usd      REAL    DEFAULT 0
);

-- ── 4. ANUNCIOS DETECTADOS (snapshot normalizado) ───────────────────
-- Meta y Google entran acá con el MISMO formato. Lo propio de cada
-- plataforma queda en metadata_json sin perderse.
CREATE TABLE IF NOT EXISTS anuncios_detectados (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    competidor_id     INTEGER NOT NULL REFERENCES competidores_seguidos(id) ON DELETE CASCADE,
    plataforma        TEXT    NOT NULL CHECK (plataforma IN ('meta','google')),
    fuente            TEXT    NOT NULL,           -- apify:apify/facebook-ads-scraper, etc.
    id_externo        TEXT,                       -- id del anuncio en la plataforma
    huella            TEXT    NOT NULL,           -- hash del contenido: detecta cambios
    anunciante        TEXT,
    titulo            TEXT,
    texto             TEXT,
    descripcion       TEXT,
    cta               TEXT,
    link_destino      TEXT,
    creativo_url      TEXT,
    tipo_creativo     TEXT,                       -- imagen | video | texto
    url_anuncio       TEXT,                       -- link a la ficha pública (trazabilidad)
    fecha_inicio      TEXT,
    fecha_fin         TEXT,
    visto_primero_en  TEXT    NOT NULL DEFAULT (datetime('now')),
    visto_ultimo_en   TEXT    NOT NULL DEFAULT (datetime('now')),
    estado            TEXT    NOT NULL DEFAULT 'activo'
                              CHECK (estado IN ('activo','pausado')),
    metadata_json     TEXT,                       -- todo lo específico de la plataforma
    analisis_json     TEXT,                       -- enriquecido por IA (ángulo, oferta...)
    corrida_id        INTEGER REFERENCES corridas_recoleccion(id),
    UNIQUE (competidor_id, plataforma, huella)
);
CREATE INDEX IF NOT EXISTS idx_anuncios_competidor ON anuncios_detectados(competidor_id);
CREATE INDEX IF NOT EXISTS idx_anuncios_visto      ON anuncios_detectados(visto_primero_en);
CREATE INDEX IF NOT EXISTS idx_anuncios_externo    ON anuncios_detectados(plataforma, id_externo);

-- ── 5. REPORTES GENERADOS ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reportes_generados (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id        INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    periodo_inicio    TEXT    NOT NULL,
    periodo_fin       TEXT    NOT NULL,
    generado_en       TEXT    NOT NULL DEFAULT (datetime('now')),
    asunto            TEXT,
    borrador_md       TEXT,                       -- (a) lo que generó la IA
    final_md          TEXT,                       -- (b) lo que vos enviaste
    datos_json        TEXT,                       -- insumo exacto usado (trazabilidad)
    estado            TEXT    NOT NULL DEFAULT 'borrador'
                              CHECK (estado IN ('borrador','revisado','enviado','descartado')),
    enviado_en        TEXT,
    canal_envio       TEXT,                       -- email:resend | email:smtp | whatsapp
    proveedor_ia      TEXT,
    modelo_ia         TEXT,
    version_prompt    TEXT,
    costo_usd         REAL    DEFAULT 0,
    validacion_json   TEXT,                       -- resultado del control de calidad
    UNIQUE (cliente_id, periodo_inicio, periodo_fin)
);
CREATE INDEX IF NOT EXISTS idx_reportes_cliente ON reportes_generados(cliente_id);

-- Qué anuncios se citaron en qué reporte: permite ir del texto a la fuente.
CREATE TABLE IF NOT EXISTS anuncios_en_reporte (
    reporte_id   INTEGER NOT NULL REFERENCES reportes_generados(id) ON DELETE CASCADE,
    anuncio_id   INTEGER NOT NULL REFERENCES anuncios_detectados(id) ON DELETE CASCADE,
    referencia   TEXT    NOT NULL,                -- la marca [A1] usada en el texto
    clasificacion TEXT,                            -- nuevo | cambiado | continua | pausado
    PRIMARY KEY (reporte_id, anuncio_id)
);

-- ── 6. EDICIONES REGISTRADAS (el dataset de la Fase 2) ──────────────
CREATE TABLE IF NOT EXISTS ediciones_registradas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    reporte_id       INTEGER NOT NULL REFERENCES reportes_generados(id) ON DELETE CASCADE,
    creado_en        TEXT    NOT NULL DEFAULT (datetime('now')),
    diff_unificado   TEXT    NOT NULL,            -- diff estándar borrador -> final
    similitud        REAL,                        -- 0..1; 1 = no cambié nada
    palabras_antes   INTEGER,
    palabras_despues INTEGER,
    etiqueta         TEXT,                        -- ej: "tono", "dato_incorrecto"
    razon            TEXT,                        -- en tus palabras, 1 línea
    bloques_json     TEXT                         -- cambios por sección/párrafo
);
CREATE INDEX IF NOT EXISTS idx_ediciones_reporte ON ediciones_registradas(reporte_id);

-- ── 7. FEEDBACK DEL CLIENTE ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feedback_cliente (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id  INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    reporte_id  INTEGER REFERENCES reportes_generados(id) ON DELETE SET NULL,
    tipo        TEXT    NOT NULL CHECK (tipo IN ('pregunta','destacado','ignorado','queja','pedido')),
    canal       TEXT,                             -- email | whatsapp | llamada | reunion
    texto       TEXT    NOT NULL,
    seccion     TEXT,                             -- a qué parte del reporte apunta
    creado_en   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_cliente ON feedback_cliente(cliente_id);

-- ── 8. REGISTRO DE USO DE IA (para cuidar el crédito) ───────────────
CREATE TABLE IF NOT EXISTS uso_ia (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    creado_en      TEXT NOT NULL DEFAULT (datetime('now')),
    tarea          TEXT NOT NULL,
    proveedor      TEXT NOT NULL,
    modelo         TEXT NOT NULL,
    tokens_entrada INTEGER DEFAULT 0,
    tokens_salida  INTEGER DEFAULT 0,
    costo_usd      REAL    DEFAULT 0,
    exito          INTEGER DEFAULT 1,
    detalle        TEXT
);
