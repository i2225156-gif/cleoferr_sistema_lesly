-- ============================================================
-- CLEOFERR - Fase 2.3: defensa en BD del descuento congelado
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   * detalle_venta.descuento_unitario es el descuento por unidad
--     CONGELADO al comprar (fase 2.3). La BD ya exige
--     descuento_unitario >= 0 (chk_detalle_descuento, db/03);
--     este script anade la cota superior faltante: el descuento
--     jamas puede superar al precio base de la misma linea.
--
--   * Defense-in-depth: el codigo del servidor (services/ofertas.py)
--     acota 0 <= descuento <= precio por construccion, pero un SQL
--     manual o un bug no deben poder persistir un descuento
--     imposible (precio final negativo). El CHECK se valida contra
--     las filas existentes al crearlo: hoy todas tienen
--     descuento_unitario = 0 (pre-chequeo 2), por lo que ninguna
--     fila lo viola.
--
-- NOTA: el backend NO depende de este constraint para funcionar
--   (el clamp vive en services/ofertas.py). Este script es solo
--   una red de seguridad adicional en la BD.
--
-- DEPENDENCIAS: db/03_oferta.sql ejecutado (columnas
--   detalle_venta.id_oferta y descuento_unitario ya existen;
--   verificado en la BD el 2026-10-12, solo lectura).
--
-- PRE-CHEQUEOS (ejecutar ANTES de este script):
--   1) El constraint NO debe existir todavia:
--      SELECT conname FROM pg_constraint
--       WHERE conrelid = 'public.detalle_venta'::regclass
--         AND conname  = 'chk_detalle_descuento_coherente';
--      -> 0 filas esperado
--   2) Ninguna fila actual puede violarlo (todo el historico tiene
--      descuento 0; si devolviera > 0, NO ejecutar: revisar esos
--      datos antes):
--      SELECT COUNT(*) AS violaciones
--        FROM public.detalle_venta
--       WHERE descuento_unitario > precio_unitario;
--      -> 0 esperado
-- ============================================================

BEGIN;

DO $ck$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.detalle_venta'::regclass
           AND conname  = 'chk_detalle_descuento_coherente'
    ) THEN
        ALTER TABLE public.detalle_venta
            ADD CONSTRAINT chk_detalle_descuento_coherente
            CHECK (descuento_unitario <= precio_unitario);
        RAISE NOTICE 'chk_detalle_descuento_coherente creado';
    ELSE
        RAISE NOTICE 'chk_detalle_descuento_coherente ya existe: nada que hacer';
    END IF;
END
$ck$;

COMMIT;

-- Si el ALTER fallara (filas que violan), la transaccion queda
-- abortada: ejecutar ROLLBACK y revisar el pre-chequeo 2.

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script):
--   SELECT conname, pg_get_constraintdef(oid)
--     FROM pg_constraint
--    WHERE conrelid = 'public.detalle_venta'::regclass
--      AND conname  = 'chk_detalle_descuento_coherente';
--   -> 1 fila:
--      chk_detalle_descuento_coherente |
--      CHECK ((descuento_unitario <= precio_unitario))
--
-- PRUEBA EN VIVO del constraint (transaccion desechable; copiar
-- tal cual: debe FALLAR y revertir con el ROLLBACK final):
--   BEGIN;
--   UPDATE public.detalle_venta
--      SET descuento_unitario = precio_unitario + 0.01
--    WHERE id_detalle = (SELECT MIN(id_detalle) FROM public.detalle_venta);
--   -- ERROR esperado: new row for relation "detalle_venta" violates
--   -- check constraint "chk_detalle_descuento_coherente"
--   -- (si la tabla estuviera vacia la prueba no falla: no hay filas;
--   --  con la verificacion posterior basta)
--   ROLLBACK;
--
-- ROLLBACK (comentado; ejecutar solo si se decide revertir):
--   ALTER TABLE public.detalle_venta
--       DROP CONSTRAINT IF EXISTS chk_detalle_descuento_coherente;
--   -- Sin dependencias: ninguna vista, trigger ni codigo del backend
--   -- requiere este constraint (es defensa adicional, no logica).
-- ============================================================
