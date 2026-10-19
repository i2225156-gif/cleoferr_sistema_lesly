-- ============================================================
-- CLEOFERR - RF17 + margen bruto: fecha_vencimiento y precio_costo
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO fue ejecutado por el asistente: redactado 2026-10-19.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo (propuesta 1b aprobada):
--   * producto.fecha_vencimiento DATE NULLABLE — productos sin control
--     de vencimiento (tornillos, herramientas) quedan en NULL: nunca se
--     inventa un vencimiento. Alimenta RF17 (vencidos/próximos a vencer
--     con alerta y reporte) y el KPI 2 (valor de stock en riesgo).
--   * producto.precio_costo NUMERIC(10,2) NULLABLE con CHECK >= 0 —
--     solo se llena cuando la dueña conozca el costo; mientras tanto el
--     KPI 1 (margen bruto) mostrará la COBERTURA ('X% de líneas con
--     costo') sin inventar márgenes.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT column_name FROM information_schema.columns
--     WHERE table_schema = 'public' AND table_name = 'producto'
--       AND column_name IN ('fecha_vencimiento', 'precio_costo');
--     -- debe devolver 0 filas (aún no existen)
--
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   SELECT column_name, data_type, is_nullable FROM information_schema.columns
--     WHERE table_schema = 'public' AND table_name = 'producto'
--       AND column_name IN ('fecha_vencimiento', 'precio_costo');
--     -> fecha_vencimiento date YES / precio_costo numeric YES
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conrelid = 'public.producto'::regclass
--       AND conname = 'producto_precio_costo_check';
--     -> CHECK (precio_costo IS NULL OR precio_costo >= 0)
--
-- PRUEBA EN VIVO de la restricción (transacción desechable; copiar en
-- el editor: el segundo INSERT debe fallar y el ROLLBACK revertir todo):
--   BEGIN;
--   INSERT INTO producto (nombre, precio, stock, precio_costo)
--   VALUES ('prueba-costo-ok', 10.00, 1, 5.00);          -- pasa
--   INSERT INTO producto (nombre, precio, stock, precio_costo)
--   VALUES ('prueba-costo-neg', 10.00, 1, -1.00);
--   -- ERROR esperado: new row violates check constraint
--   --                 "producto_precio_costo_check"
--   ROLLBACK;
-- ============================================================

BEGIN;

ALTER TABLE public.producto
    ADD COLUMN IF NOT EXISTS fecha_vencimiento DATE;

ALTER TABLE public.producto
    ADD COLUMN IF NOT EXISTS precio_costo NUMERIC(10,2);

DO $chk$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'public.producto'::regclass
                      AND conname = 'producto_precio_costo_check')
    THEN
        ALTER TABLE public.producto
            ADD CONSTRAINT producto_precio_costo_check
            CHECK (precio_costo IS NULL OR precio_costo >= 0);
    END IF;
END
$chk$;

COMMIT;

-- ============================================================
-- DESPUÉS DE EJECUTAR (el asistente lo implementará en commits
-- separados, con tu OK): formulario de producto con los dos campos,
-- alerta y reporte de vencidos/próximos a vencer con umbral por la
-- variable de entorno VENCIMIENTO_UMBRAL_DIAS (default 30), columna
-- y reporte, ruta POST /productos/desactivar/<id> con CSRF, y los
-- KPIs 1 (margen bruto con cobertura) y 2 (stock en riesgo).
--
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   ALTER TABLE public.producto
--       DROP CONSTRAINT IF EXISTS producto_precio_costo_check;
--   ALTER TABLE public.producto
--       DROP COLUMN IF EXISTS fecha_vencimiento;
--   ALTER TABLE public.producto
--       DROP COLUMN IF EXISTS precio_costo;
--   -- Verificar antes que no haya datos cargados que se quieran
--   -- conservar:
--   --   SELECT COUNT(*) FROM producto WHERE fecha_vencimiento IS NOT NULL
--   --      OR precio_costo IS NOT NULL;
-- ============================================================
