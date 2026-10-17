-- ============================================================
-- CLEOFERR - CORRECCION (grupos 1 y 2): CANCELAR ventas pendientes
--          legacy (pago sin referencia) o sin fila de pago
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase, tras revisar
--          la LISTA de ids con los PRE-CHEQUEOS.
--          NO fue ejecutado por el asistente: redactado 2026-10-12.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo (decisión del propietario, opción CANCELAR):
--   * Cancelar las ventas PENDIENTES de los grupos 1 y 2:
--       Grupo 1 (pago legacy sin id_transaccion):
--         22, 24, 25, 26, 34, 45, 73
--       Grupo 2 (sin fila de pago):
--         52, 53, 54, 55, 58, 59, 66, 86, 87, 88, 89, 90, 91
--   * Devolver el stock con los marcadores EXACTOS del helper B2
--     ('Devolución stock venta #<id> (producto #<pid>)'): este script y
--     la app comparten la misma idempotencia.
--   * Marcar 'cancelado' el pago legacy pendiente de esas ventas
--     (estado existente tras db/06). El monto del pago NO se toca.
--   * El trigger trg_auditar_estado_venta registra cada cambio en
--     auditoria (valor_anterior 'pendiente' -> valor_nuevo 'cancelado').
--
-- SALVAGUARDAS:
--   * Solo toca ventas de la lista que SIGAN 'pendientes' y cuyo pago
--     (si existe) NO esté 'confirmado': si alguna cambió de estado desde
--     la revisión, se salta y queda visible en la VERIFICACION.
--   * NO incluye la venta 223 (el propietario la cancela desde el sistema).
--   * NO toca los 9 pagos legacy pendientes sobre ventas NO pendientes.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   1) Revisar la lista y su estado actual:
--   SELECT v.id_venta, ev.nombre AS estado, COALESCE(p.estado, 'SIN_PAGO') AS pago
--     FROM venta v
--     LEFT JOIN pago p ON p.id_venta = v.id_venta
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE v.id_venta IN (22,24,25,26,34,45,73,52,53,54,55,58,59,66,
--                         86,87,88,89,90,91)
--    ORDER BY v.id_venta;
--   2) Stock que se devolvería:
--   SELECT v.id_venta, d.id_producto, d.cantidad, d.precio_unitario
--     FROM venta v JOIN detalle_venta d ON d.id_venta = v.id_venta
--    WHERE v.id_venta IN (22,24,25,26,34,45,73,52,53,54,55,58,59,66,
--                         86,87,88,89,90,91)
--    ORDER BY v.id_venta, d.id_producto;
-- ============================================================

BEGIN;

DO $corr$
DECLARE
    ids          BIGINT[] := ARRAY[22,24,25,26,34,45,73,
                                  52,53,54,55,58,59,66,86,87,88,89,90,91]::bigint[];
    vid          BIGINT;
    estado       TEXT;
    r            RECORD;
    marca        TEXT;
    stock_nuevo  INTEGER;
BEGIN
    FOREACH vid IN ARRAY ids
    LOOP
        -- Bloquear la fila y leer su estado actual
        SELECT ev.nombre INTO estado
          FROM venta v
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
         WHERE v.id_venta = vid
           FOR UPDATE OF v;

        -- Salvaguarda: solo si sigue pendiente y sin pago confirmado
        IF estado = 'pendiente' AND NOT EXISTS (
               SELECT 1 FROM pago p
                WHERE p.id_venta = vid AND p.estado = 'confirmado')
        THEN
            UPDATE venta
               SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
                                        WHERE nombre = 'cancelado')
             WHERE id_venta = vid;

            -- Devolución de stock con marcadores B2 EXACTOS (idempotente
            -- por marca, igual que services/inventario.py)
            FOR r IN
                SELECT d.id_producto, d.cantidad, d.precio_unitario
                  FROM detalle_venta d
                 WHERE d.id_venta = vid
                   AND NOT EXISTS (
                        SELECT 1 FROM inventario_movimiento m
                         WHERE m.observacion = 'Devolución stock venta #'
                                        || vid || ' (producto #' || d.id_producto || ')')
            LOOP
                marca := 'Devolución stock venta #' || vid
                         || ' (producto #' || r.id_producto || ')';
                UPDATE producto
                   SET stock = stock + r.cantidad
                 WHERE id_producto = r.id_producto
                RETURNING stock INTO stock_nuevo;
                INSERT INTO inventario_movimiento
                    (tipo, id_producto, id_proveedor, cantidad, precio_unitario,
                     observacion, id_usuario, stock_resultante)
                VALUES ('entrada', r.id_producto, NULL, r.cantidad, r.precio_unitario,
                        marca, NULL, stock_nuevo);
            END LOOP;

            -- Pago legacy pendiente -> cancelado (el MONTO no se toca)
            UPDATE pago
               SET estado = 'cancelado'
             WHERE id_venta = vid AND estado = 'pendiente';
        END IF;
    END LOOP;
END
$corr$;

COMMIT;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   1) Todas las ventas de la lista en 'cancelado' y su pago coherente:
--   SELECT v.id_venta, ev.nombre AS estado, COALESCE(p.estado, 'SIN_PAGO') AS pago
--     FROM venta v
--     LEFT JOIN pago p ON p.id_venta = v.id_venta
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE v.id_venta IN (22,24,25,26,34,45,73,52,53,54,55,58,59,66,
--                         86,87,88,89,90,91)
--    ORDER BY v.id_venta;
--   2) Movimientos de devolución generados (uno por producto devuelto):
--   SELECT observacion, cantidad, stock_resultante
--     FROM inventario_movimiento
--    WHERE observacion ~ '^Devolución stock venta #(22|24|25|26|34|45|73|52|53|54|55|58|59|66|86|87|88|89|90|91) '
--    ORDER BY observacion;
--   3) Auditoria: +1 fila por cada venta cancelada (trigger):
--   SELECT COUNT(*) FROM auditoria;
--   4) Ninguna venta de la lista quedó pendiente:
--   SELECT COUNT(*) FROM venta v
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE ev.nombre = 'pendiente'
--      AND v.id_venta IN (22,24,25,26,34,45,73,52,53,54,55,58,59,66,
--                          86,87,88,89,90,91);   -- 0 esperado
--
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   ADVERTENCIA: la reversión re-resta stock que pudo venderse desde
--   entonces; revisar cada caso antes. Por cada venta revertida:
--     UPDATE venta
--        SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
--                                WHERE nombre = 'pendiente')
--      WHERE id_venta IN (22,24,25,26,34,45,73,52,53,54,55,58,59,66,
--                         86,87,88,89,90,91)
--        AND id_estado_venta = (SELECT id_estado_venta FROM estado_venta
--                                WHERE nombre = 'cancelado');
--     UPDATE producto p
--        SET stock = stock - d.cantidad
--       FROM detalle_venta d
--      WHERE d.id_venta IN (22,24,25,26,34,45,73,52,53,54,55,58,59,66,
--                           86,87,88,89,90,91)
--        AND p.id_producto = d.id_producto;
--     DELETE FROM inventario_movimiento
--      WHERE observacion ~ '^Devolución stock venta #(22|24|25|26|34|45|73|52|53|54|55|58|59|66|86|87|88|89|90|91) ';
--     UPDATE pago SET estado = 'pendiente'
--      WHERE id_venta IN (22,24,25,26,34,45,73)
--        AND estado = 'cancelado';
--   (El trigger auditará también estas reversiones.)
-- ============================================================
