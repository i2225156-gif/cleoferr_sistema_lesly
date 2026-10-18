-- ============================================================
-- CLEOFERR - CORRECCION PUNTUAL (Bloque 2): devolver el stock de la
-- venta online #50, cancelada ANTES de que existiera el helper B2.
-- BD: TIENDA (Supabase PostgreSQL).
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO FUE EJECUTADA por el asistente: decide el propietario.
-- Idempotente: usa el MISMO marcador que el helper B2
--   (services/inventario.py):
--   'Devolución stock venta #50 (producto #N)'
--   Si el movimiento ya existe, no hace nada. Compatible con B2: si
--   B2 volviera a actuar sobre esta venta, tampoco duplicaria.
--
-- ANALISIS (solo lectura, 2026-10-11):
--   * Venta online cancelada: #50 (2026-09-21, cliente id 64). Es la
--     UNICA venta online cancelada (cancelada antes de B2).
--   * Salidas registradas: producto 6 (1 u, stock_resultante 7) y
--     producto 8 (1 u, stock_resultante 26). NINGUNA entrada de
--     devolucion existe (verificado por igualdad de marcador).
--   * Stock actual: producto 6 = 0, producto 8 = 12.
--   * FALTA devolver: producto 6 -> 1 unidad; producto 8 -> 1 unidad.
--     Tras la correccion quedaria: producto 6 = 1, producto 8 = 13.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT v.id_venta, ev.nombre FROM venta v
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE v.id_venta = 50;                          -- debe: 'cancelado'
--   SELECT observacion FROM inventario_movimiento
--    WHERE observacion IN ('Devolución stock venta #50 (producto #6)',
--                          'Devolución stock venta #50 (producto #8)');
--     -> 0 filas (aun sin devolver)
--   SELECT id_producto, stock FROM producto WHERE id_producto IN (6, 8);
--     -> producto 6 = 0, producto 8 = 12 (estado actual)
-- ============================================================

DO $corr$
DECLARE
    r RECORD;
    stock_nuevo INTEGER;
    marca TEXT;
BEGIN
    FOR r IN
        SELECT d.id_producto, d.cantidad, d.precio_unitario
          FROM public.detalle_venta d
         WHERE d.id_venta = 50
           AND NOT EXISTS (
                SELECT 1 FROM public.inventario_movimiento m
                 WHERE m.observacion =
                       'Devolución stock venta #50 (producto #' || d.id_producto || ')')
    LOOP
        marca := 'Devolución stock venta #50 (producto #' || r.id_producto || ')';

        UPDATE public.producto
           SET stock = stock + r.cantidad
         WHERE id_producto = r.id_producto
        RETURNING stock INTO stock_nuevo;

        INSERT INTO public.inventario_movimiento
            (tipo, id_producto, id_proveedor, cantidad, precio_unitario,
             observacion, id_usuario, stock_resultante)
        VALUES ('entrada', r.id_producto, NULL, r.cantidad, r.precio_unitario,
                marca, NULL, stock_nuevo);
    END LOOP;
END
$corr$;

-- ============================================================
-- VERIFICACION POSTERIOR (solo lectura):
--   SELECT observacion, cantidad, stock_resultante
--     FROM inventario_movimiento
--    WHERE observacion IN ('Devolución stock venta #50 (producto #6)',
--                          'Devolución stock venta #50 (producto #8)');
--     -> 2 filas, cantidad 1 y 1, stock_resultante 1 y 13
--   SELECT id_producto, stock FROM producto WHERE id_producto IN (6, 8);
--     -> producto 6 = 1, producto 8 = 13
--   SELECT count(*) FROM inventario_movimiento
--    WHERE observacion LIKE 'Devolución stock venta #50%';  -- 2 exactas
--
-- ROLLBACK (comentado; ejecutar solo si se decide revertir):
--   UPDATE producto SET stock = stock - 1 WHERE id_producto = 6;
--   UPDATE producto SET stock = stock - 1 WHERE id_producto = 8;
--   DELETE FROM inventario_movimiento
--    WHERE observacion IN ('Devolución stock venta #50 (producto #6)',
--                          'Devolución stock venta #50 (producto #8)');
-- ============================================================
