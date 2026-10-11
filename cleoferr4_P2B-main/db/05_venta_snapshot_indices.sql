-- ============================================================
-- CLEOFERR - B4: snapshot del cliente en venta + indices de consulta
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   * venta.cliente_nombre / venta.cliente_documento: snapshot del
--     cliente al momento de la venta, para que pedidos y comprobantes
--     no dependan de que el cliente siga existiendo en la BD AUTH
--     (cross-BD: FK imposible hacia cliente).
--   * Indice inventario_movimiento(fecha) para reportes por fecha.
--
-- DEPENDENCIAS:
--   * El backfill COMPLETO de los clientes existentes requiere el
--     archivo EXTERNO generado (fuera del repositorio porque contiene
--     datos personales):
--     C:\Users\pc\AppData\Local\Temp\opencode\backfill_venta_clientes.sql
--     (34 UPDATEs; ejecutarlo DESPUES de este script).
--   * El codigo que use el snapshot (pedidos/comprobantes) requiere
--     este script ejecutado.
--
-- VERIFICACIONES PREVIAS HECHAS EL 2026-10-11 (solo lectura):
--   * Columnas nuevas inexistentes en venta (0 filas):
--     SELECT column_name FROM information_schema.columns
--       WHERE table_schema='public' AND table_name='venta'
--         AND column_name IN ('cliente_nombre','cliente_documento');
--   * Son NULLABLE -> ningun dato actual puede violarlas.
--   * Huerfanos reales: venta.id_cliente usados = {11,12,13,20,23,24,
--     49,62,63,64,71,72,74}; en AUTH existen todos MENOS 20 y 49.
--     -> 4 ventas huerfanas (3 del cliente 20, 1 del 49).
--   * 1 venta con id_cliente NULL (sin cliente): queda sin snapshot
--     (NULL) por diseno: no hay dato con que rellenarla.
--   * inventario_movimiento(fecha): la columna existe y NO hay indice
--     de fecha hoy (solo idx_inv_mov_producto y la PK).
--
-- DECISION (NO se crea aqui, ya cubierto):
--   * pago(id_venta): ya cubierto por uq_pago_venta (db/02).
--   * UNIQUE de venta.num_operacion: ya creado en db/02
--     (uq_venta_num_operacion). No repetir.
-- ============================================================

-- 1) Columnas snapshot (nullable: las ventas viejas empiezan en NULL).
ALTER TABLE public.venta
    ADD COLUMN IF NOT EXISTS cliente_nombre VARCHAR(120),
    ADD COLUMN IF NOT EXISTS cliente_documento VARCHAR(20);

-- 2) Backfill de HUERFANOS: texto generico, sin datos personales.
--    Ids verificados el 2026-10-11 contra la BD AUTH (no existen alli).
--    Idempotente: solo rellena filas aun vacias.
UPDATE public.venta
   SET cliente_nombre = 'Cliente no registrado (ref. #' || id_cliente || ')'
 WHERE id_cliente IN (20, 49)
   AND cliente_nombre IS NULL;

-- 3) Indice de fecha para reportes y consultas de inventario.
CREATE INDEX IF NOT EXISTS idx_inventario_movimiento_fecha
    ON public.inventario_movimiento (fecha);

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script):
--   SELECT column_name FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='venta'
--       AND column_name IN ('cliente_nombre','cliente_documento');  -- 2 filas
--   SELECT id_venta, id_cliente, cliente_nombre FROM public.venta
--     WHERE id_cliente IN (20, 49);   -- 4 filas con el texto generico
--   SELECT indexname FROM pg_indexes
--     WHERE schemaname='public' AND tablename='inventario_movimiento'
--       AND indexname='idx_inventario_movimiento_fecha';            -- 1 fila
--
-- Despues de este script, ejecutar el archivo externo de backfill
-- (34 UPDATEs con los datos reales de AUTH) y verificar:
--   SELECT COUNT(*) FROM public.venta
--     WHERE id_cliente IS NOT NULL AND id_cliente NOT IN (20, 49)
--       AND cliente_nombre IS NULL;                                 -- 0 esperado
--
-- ROLLBACK (comentado; ejecutar solo si se decide revertir):
--   DROP INDEX IF EXISTS idx_inventario_movimiento_fecha;
--   UPDATE public.venta SET cliente_nombre = NULL
--     WHERE cliente_nombre LIKE 'Cliente no registrado (ref. #%';
--   ALTER TABLE public.venta DROP COLUMN IF EXISTS cliente_documento;
--   ALTER TABLE public.venta DROP COLUMN IF EXISTS cliente_nombre;
--   (El archivo externo se revierte solo con las columnas: al
--    eliminarlas desaparece el snapshot completo.)
-- ============================================================
