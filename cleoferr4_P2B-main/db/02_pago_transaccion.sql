-- ============================================================
-- CLEOFERR - FASE 2.1-a: pago como registro de transaccion de pasarela
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   * pago deja de ser una fila suelta y pasa a registrar la transaccion
--     de la pasarela (simulada o real): id de transaccion, pasarela, monto
--     y traza de quien/cuando confirmo.
--   * UNIQUE(pago.id_venta): un solo pago por venta. La confirmacion o el
--     rechazo son UPDATEs sobre la MISMA fila (maquina de estados
--     pendiente -> confirmado | rechazado), nunca INSERTs duplicados.
--     Esto ademas da el indice que falta hoy sobre pago.id_venta.
--   * UNIQUE parcial de venta.num_operacion: el "pago por referencia"
--     (N.o de operacion) no puede repetirse entre ventas.
--
-- PRE-CHEQUEOS (deben devolver 0 filas ANTES de ejecutar esto):
--   SELECT id_venta, COUNT(*) FROM pago GROUP BY id_venta HAVING COUNT(*) > 1;
--   SELECT num_operacion, COUNT(*) FROM venta
--     WHERE num_operacion IS NOT NULL
--     GROUP BY num_operacion HAVING COUNT(*) > 1;
--   Estado verificado el 04/10/2026: 0 filas en ambos (24 pagos unicos,
--   39 ventas con num_operacion NULL).
--
-- RLS: pago ya tiene RLS activo y 0 politicas (verificado 04/10/2026);
-- este script no crea tablas nuevas, por lo que no toca RLS.
-- ============================================================

-- 1) Columnas nuevas de pago (idempotente)
ALTER TABLE pago
    ADD COLUMN IF NOT EXISTS id_transaccion VARCHAR(64),
    ADD COLUMN IF NOT EXISTS pasarela       VARCHAR(20),
    ADD COLUMN IF NOT EXISTS monto          NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS confirmado_por BIGINT,
    ADD COLUMN IF NOT EXISTS confirmado_en  TIMESTAMPTZ;
-- confirmado_por: id de usuario de la BD AUTH (cross-BD: FK imposible).
-- NULL cuando la "confirmacion" la hace la pasarela automaticamente.

-- 2) CHECK de monto (idempotente via pg_constraint)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_pago_monto') THEN
        ALTER TABLE pago ADD CONSTRAINT chk_pago_monto
            CHECK (monto IS NULL OR monto > 0);
    END IF;
END $$;

-- 3) Un pago por venta (idempotente; PRE: pre-chequeo 1 en 0 filas)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_pago_venta') THEN
        ALTER TABLE pago ADD CONSTRAINT uq_pago_venta UNIQUE (id_venta);
    END IF;
END $$;

-- 4) num_operacion unico cuando existe (indice parcial, idempotente)
CREATE UNIQUE INDEX IF NOT EXISTS uq_venta_num_operacion
    ON venta (num_operacion)
    WHERE num_operacion IS NOT NULL;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script):
--   SELECT column_name, data_type FROM information_schema.columns
--     WHERE table_name = 'pago' ORDER BY ordinal_position;
--     -> debe listar 11 columnes (6 originales + 5 nuevas).
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conrelid = 'pago'::regclass ORDER BY contype, conname;
--     -> debe incluir uq_pago_venta y chk_pago_monto.
--   SELECT indexname FROM pg_indexes
--     WHERE tablename IN ('pago','venta') ORDER BY tablename, indexname;
--     -> debe incluir uq_venta_num_operacion.
--   SELECT estado, COUNT(*) FROM pago GROUP BY estado;
--     -> sin cambios: 16 pendiente / 8 confirmado.
-- ============================================================
-- ROLLBACK (comentado; ejecutar solo si se decide revertir):
--   ALTER TABLE pago DROP CONSTRAINT IF EXISTS uq_pago_venta;
--   ALTER TABLE pago DROP CONSTRAINT IF EXISTS chk_pago_monto;
--   DROP INDEX IF EXISTS uq_venta_num_operacion;
--   ALTER TABLE pago
--       DROP COLUMN IF EXISTS confirmado_en,
--       DROP COLUMN IF EXISTS confirmado_por,
--       DROP COLUMN IF EXISTS monto,
--       DROP COLUMN IF EXISTS pasarela,
--       DROP COLUMN IF EXISTS id_transaccion;
-- ============================================================
