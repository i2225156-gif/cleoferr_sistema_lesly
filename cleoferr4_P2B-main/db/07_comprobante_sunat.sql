-- ============================================================
-- CLEOFERR - FASE 2.2: soporte SUNAT simulado en comprobante
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO fue ejecutado por el asistente: redactado 2026-10-11.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   * comprobante.referencia_proveedor: referencia NEUTRAL del proveedor
--     de facturación (SIM-... en el simulado; la del PSE/OSE a futuro).
--   * UNIQUE(serie, numero): red de seguridad para la asignación
--     concurrente de correlativos (D1: solo los consumen ventas pagadas).
--   * CHECK estado_sunat + 'anulado': solo HABILITA el valor; la anulación
--     real no está implementada (requiere tu OK aparte).
--
-- DEPENDENCIAS: ninguna nueva (tabla comprobante existente).
--   El CODIGO de 2.2 (emisión al confirmar pago, rutas del comprobante del
--   cliente, rótulo SIMULADO, EMPRESA_RUC) requiere ESTE script ejecutado.
--
-- VERIFICACIONES PREVIAS HECHAS EL 2026-10-11 (solo lectura):
--   * referencia_proveedor no existe (0 columnas).
--   * CHECK actual: estado_sunat IN ('pendiente','no_aplica','enviado').
--   * Valores existentes: 18 filas, TODAS 'no_aplica'
--     -> ampliar el CHECK no puede violar datos actuales.
--   * Duplicados (serie, numero): 0 filas -> el UNIQUE es seguro hoy.
--   * Sin colisión de nombres (uq_comprobante_serie_numero).
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT COUNT(*) FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='comprobante'
--       AND column_name='referencia_proveedor';       -- 0 esperado
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conname='comprobante_estado_sunat_check';  -- def. actual (3 valores)
--   SELECT estado_sunat, COUNT(*) FROM comprobante
--     GROUP BY estado_sunat;                           -- solo valores actuales
--   SELECT serie, numero FROM comprobante
--     GROUP BY serie, numero HAVING COUNT(*) > 1;      -- 0 filas esperado
-- ============================================================

BEGIN;

-- 1) Referencia neutral del proveedor de facturación.
ALTER TABLE public.comprobante
    ADD COLUMN IF NOT EXISTS referencia_proveedor VARCHAR(64);

-- 2) Un comprobante por par serie-correlativo (idempotente por nombre).
DO $uq$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_comprobante_serie_numero') THEN
        ALTER TABLE public.comprobante
            ADD CONSTRAINT uq_comprobante_serie_numero UNIQUE (serie, numero);
    END IF;
END
$uq$;

-- 3) Habilitar 'anulado' en estado_sunat (idempotente: solo se reemplaza
--    si todavía no incluye 'anulado', o si el CHECK fue borrado).
DO $ck$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'comprobante_estado_sunat_check'
                  AND pg_get_constraintdef(oid) NOT LIKE '%anulado%')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'comprobante_estado_sunat_check')
    THEN
        ALTER TABLE public.comprobante
            DROP CONSTRAINT IF EXISTS comprobante_estado_sunat_check;
        ALTER TABLE public.comprobante ADD CONSTRAINT comprobante_estado_sunat_check
            CHECK (estado_sunat IN ('pendiente', 'no_aplica', 'enviado', 'anulado'));
    END IF;
END
$ck$;

COMMIT;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   SELECT COUNT(*) FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='comprobante'
--       AND column_name='referencia_proveedor';        -- 1
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conrelid = 'public.comprobante'::regclass ORDER BY conname;
--     -> incluye uq_comprobante_serie_numero y el CHECK con 'anulado'
--
-- PRUEBA EN VIVO del UNIQUE (transaccion desechable; copiar en el
-- editor: el UPDATE que duplica el par serie-numero debe fallar y el
-- ROLLBACK revertir todo):
--   BEGIN;
--   UPDATE comprobante SET serie = 'B999', numero = 'N-PRUEBA'
--    WHERE id_comprobante IN (SELECT id_comprobante FROM comprobante
--                              ORDER BY id_comprobante LIMIT 2);
--   -- ERROR esperado: duplicate key value violates unique constraint
--   --                 "uq_comprobante_serie_numero"
--   ROLLBACK;
--
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   ALTER TABLE public.comprobante
--       DROP CONSTRAINT IF EXISTS uq_comprobante_serie_numero;
--   -- Restaurar el CHECK original SOLO si ninguna fila usa 'anulado'
--   -- (verificar antes: SELECT COUNT(*) FROM comprobante
--   --   WHERE estado_sunat = 'anulado';   -> debe dar 0):
--   ALTER TABLE public.comprobante
--       DROP CONSTRAINT IF EXISTS comprobante_estado_sunat_check;
--   ALTER TABLE public.comprobante ADD CONSTRAINT comprobante_estado_sunat_check
--       CHECK (estado_sunat IN ('pendiente', 'no_aplica', 'enviado'));
--   ALTER TABLE public.comprobante DROP COLUMN IF EXISTS referencia_proveedor;
-- ============================================================
