-- ============================================================
-- CLEOFERR - Documento de identidad del proveedor (RUC / DNI / CE)
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- *** EJECUTAR EN EL PROYECTO TIENDA, NO EN AUTH ***
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO fue ejecutado por el asistente: redactado 2026-10-28.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   * proveedor.tipo_documento VARCHAR(3) NULL con CHECK en
--     ('RUC', 'DNI', 'CE') y proveedor.numero_documento VARCHAR(12)
--     NULL: el documento es opcional en la BD (los proveedores actuales
--     quedan sin documento hasta que se cargue), pero OBLIGATORIO en el
--     formulario/validación de servidor (services/validacion.documento_proveedor).
--   * Índice único parcial uq_proveedor_documento (tipo_documento,
--     numero_documento) WHERE numero_documento IS NOT NULL: no puede
--     haber dos proveedores con el mismo documento, y los proveedores
--     SIN documento (numero_documento IS NULL) quedan fuera del índice
--     para permitirlos en cantidad.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT column_name FROM information_schema.columns
--     WHERE table_schema = 'public' AND table_name = 'proveedor'
--       AND column_name IN ('tipo_documento', 'numero_documento');
--     -- debe devolver 0 filas (aún no existen)
--
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   SELECT column_name, data_type, is_nullable
--     FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name = 'proveedor'
--      AND column_name IN ('tipo_documento', 'numero_documento');
--     -> tipo_documento character varying(3) YES
--        numero_documento character varying(12) YES
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conrelid = 'public.proveedor'::regclass
--       AND conname = 'proveedor_tipo_documento_check';
--     -> CHECK (((tipo_documento)::text = ANY ((ARRAY['RUC'::character
--            varying, 'DNI'::character varying, 'CE'::character
--            varying])::text[])))
--   SELECT indexname, indexdef FROM pg_indexes
--    WHERE schemaname = 'public' AND indexname = 'uq_proveedor_documento';
--     -> CREATE UNIQUE INDEX ... ON public.proveedor USING btree
--            (tipo_documento, numero_documento)
--        WHERE numero_documento IS NOT NULL
--
-- PRUEBA EN VIVO del índice (transacción desechable; copiar en el
-- editor: el segundo INSERT debe fallar y el ROLLBACK revertir todo):
--   BEGIN;
--   INSERT INTO proveedor (nombre, tipo_documento, numero_documento)
--          VALUES ('prueba-doc-a', 'RUC', '20123456786');              -- pasa
--   INSERT INTO proveedor (nombre, tipo_documento, numero_documento)
--          VALUES ('prueba-doc-b', 'RUC', '20123456786');
--   -- ERROR esperado: duplicate key value violates unique constraint
--   --                 "uq_proveedor_documento"
--   INSERT INTO proveedor (nombre, tipo_documento, numero_documento)
--          VALUES ('prueba-doc-n', NULL, NULL);                        -- pasa (sin documento)
--   ROLLBACK;
-- ============================================================

BEGIN;

ALTER TABLE public.proveedor
    ADD COLUMN IF NOT EXISTS tipo_documento   VARCHAR(3),
    ADD COLUMN IF NOT EXISTS numero_documento VARCHAR(12);

DO $chk$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'public.proveedor'::regclass
                      AND conname = 'proveedor_tipo_documento_check')
    THEN
        ALTER TABLE public.proveedor
            ADD CONSTRAINT proveedor_tipo_documento_check
            CHECK (tipo_documento IS NULL OR
                   tipo_documento IN ('RUC', 'DNI', 'CE'));
    END IF;
END
$chk$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_proveedor_documento
    ON public.proveedor (tipo_documento, numero_documento)
    WHERE numero_documento IS NOT NULL;

COMMIT;

-- ============================================================
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   DROP INDEX IF EXISTS public.uq_proveedor_documento;
--   ALTER TABLE public.proveedor
--       DROP CONSTRAINT IF EXISTS proveedor_tipo_documento_check;
--   ALTER TABLE public.proveedor
--       DROP COLUMN IF EXISTS numero_documento,
--       DROP COLUMN IF EXISTS tipo_documento;
--   -- Verificar antes si hay documentos cargados que se quieran
--   -- conservar:
--   --   SELECT COUNT(*) FROM proveedor
--   --    WHERE numero_documento IS NOT NULL;
-- ============================================================
