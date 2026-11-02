-- ============================================================
-- CLEOFERR - AUTH-04: consentimiento de privacidad (Ley 29733)
-- !! EJECUTAR EN EL PROYECTO AUTH, NO EN TIENDA !!
-- BD: AUTH (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase del proyecto
--          AUTH. NO fue ejecutado por el asistente: redactado 2026-10-27.
-- Idempotente: ADD COLUMN IF NOT EXISTS permite re-ejecutarlo sin
--          duplicar nada ni fallar si ya está aplicado.
-- ============================================================
-- Objetivo:
--   Dejar constancia del consentimiento de la Política de Privacidad
--   en el registro de clientes (Ley 29733 - Ley de Protección de
--   Datos Personales):
--     * acepta_privacidad:        TRUE solo si el cliente marcó la
--                                 casilla obligatoria (DEFAULT FALSE:
--                                 nunca se asume el consentimiento).
--     * fecha_acepta_privacidad:  momento exacto del consentimiento
--                                 (TIMESTAMPTZ, lo llena la app con
--                                 NOW() al registrar).
--     * version_privacidad:       versión del texto aceptado ('1.0').
--     * acepta_publicidad:        finalidad OPCIONAL de ofertas y
--                                 novedades; TRUE solo con la casilla
--                                 marcada.
--
--   Mientras esta migración NO esté aplicada, la aplicación registra
--   al cliente SIN estos campos y deja una advertencia en el log
--   (requisito: el registro no debe romperse). La consulta que usa
--   la app para decidirlo es la misma del bloque de verificación.
--
-- PRE-CHEQUEO (ejecutar ANTES; solo lectura):
--   SELECT column_name FROM information_schema.columns
--    WHERE table_schema = CURRENT_SCHEMA() AND table_name = 'cliente';
--     -> ver las columnas actuales de cliente.
-- ============================================================

BEGIN;

ALTER TABLE cliente
    ADD COLUMN IF NOT EXISTS acepta_privacidad        BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS fecha_acepta_privacidad  TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS version_privacidad       VARCHAR(10) NULL,
    ADD COLUMN IF NOT EXISTS acepta_publicidad        BOOLEAN     NOT NULL DEFAULT FALSE;

COMMIT;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   SELECT column_name, data_type, is_nullable, column_default
--     FROM information_schema.columns
--    WHERE table_schema = CURRENT_SCHEMA()
--      AND table_name = 'cliente'
--      AND column_name IN ('acepta_privacidad', 'fecha_acepta_privacidad',
--                          'version_privacidad', 'acepta_publicidad');
--     -> deben aparecer las 4 columnas con sus tipos.
--
--   SELECT COUNT(*) AS n FROM information_schema.columns
--    WHERE table_schema = CURRENT_SCHEMA()
--      AND table_name = 'cliente'
--      AND column_name IN ('acepta_privacidad', 'fecha_acepta_privacidad',
--                          'version_privacidad', 'acepta_publicidad');
--     -> 4 esperado (misma consulta que usa registro_cliente para
--        decidir si guarda o no los campos).
--
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   ALTER TABLE cliente
--       DROP COLUMN IF EXISTS acepta_privacidad,
--       DROP COLUMN IF EXISTS fecha_acepta_privacidad,
--       DROP COLUMN IF EXISTS version_privacidad,
--       DROP COLUMN IF EXISTS acepta_publicidad;
--   -- OJO: al revertir se PIERDE la constancia del consentimiento de
--   -- privacidad de los clientes ya registrados. Si ya hay filas con
--   -- esos datos, exporta un respaldo ANTES de revertir.
-- ============================================================
