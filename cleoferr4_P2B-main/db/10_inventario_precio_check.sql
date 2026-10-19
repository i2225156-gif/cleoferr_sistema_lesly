-- ============================================================
-- CLEOFERR - FASE 2: CHECK faltante en inventario_movimiento
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO fue ejecutado por el asistente: redactado 2026-10-19.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo: la auditoría de FASE 2 rechaza en el SERVIDOR los precios
--   unitarios negativos del movimiento de inventario, pero la BD seguía
--   aceptándolos (la columna es numeric nullable SIN CHECK). Este
--   script añade la última barrera:
--     CHECK (precio_unitario IS NULL OR precio_unitario >= 0)
--   (cantidad > 0 y tipo entrada/salida ya tienen CHECK desde el origen)
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT COUNT(*) FROM inventario_movimiento
--     WHERE precio_unitario < 0;                  -- debe ser 0
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conrelid = 'public.inventario_movimiento'::regclass
--     ORDER BY conname;        -- no debe existir inventario_precio_check
--
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conrelid = 'public.inventario_movimiento'::regclass
--     ORDER BY conname;
--     -> debe listar inventario_precio_check con la definición nueva
--
-- PRUEBA EN VIVO de la restricción (transacción desechable; copiar en
-- el editor: el INSERT debe fallar y el ROLLBACK revertir todo):
--   BEGIN;
--   INSERT INTO inventario_movimiento
--       (tipo, id_producto, cantidad, precio_unitario)
--   VALUES ('entrada', (SELECT id_producto FROM producto LIMIT 1),
--           1, -1.00);
--   -- ERROR esperado: new row violates check constraint
--   --                 "inventario_precio_check"
--   ROLLBACK;
-- ============================================================

BEGIN;

DO $chk$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'public.inventario_movimiento'::regclass
                      AND conname = 'inventario_precio_check')
    THEN
        ALTER TABLE public.inventario_movimiento
            ADD CONSTRAINT inventario_precio_check
            CHECK (precio_unitario IS NULL OR precio_unitario >= 0);
    END IF;
END
$chk$;

COMMIT;

-- ============================================================
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   ALTER TABLE public.inventario_movimiento
--       DROP CONSTRAINT IF EXISTS inventario_precio_check;
--   -- Verificar antes que ninguna fila use valores negativos:
--   --   SELECT COUNT(*) FROM inventario_movimiento
--   --     WHERE precio_unitario < 0;   -- debe seguir siendo 0
-- ============================================================
