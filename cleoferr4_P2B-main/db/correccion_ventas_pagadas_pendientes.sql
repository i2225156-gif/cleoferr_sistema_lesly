-- ============================================================
-- CLEOFERR - CORRECCION (grupo 4): ventas PENDIENTES con pago CONFIRMADO
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO fue ejecutado por el asistente: redactado 2026-10-12.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo (D1): solo las ventas PAGadas avanzan; una venta cuyo pago
--   está CONFIRMADO no puede seguir figurando como 'pendiente'. Este
--   script alinea el estado de la venta con el de su pago (uq_pago_venta
--   garantiza un único pago por venta, así que el EXISTS es seguro).
--   El trigger trg_auditar_estado_venta registra el cambio en auditoria.
--
-- INVERSO del caso 223: este script NO toca ventas confirmadas con pago
--   pendiente (como la venta 223 del propietario); eso lo decide el
--   propietario aparte.
--
-- LISTADO PREVIO VERIFICADO EL 2026-10-12 (solo lectura):
--   * Grupo 4 (pago confirmado + venta pendiente): 1 venta -> id 32
--     (pago 26, monto NULL, id_transaccion NULL, total detalle 71.50).
--   * Tras ejecutar: 0 ventas en esa condición.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT v.id_venta, p.id_pago, p.estado
--     FROM venta v JOIN pago p ON p.id_venta = v.id_venta
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE ev.nombre = 'pendiente' AND p.estado = 'confirmado';
--     -- lista de afectados (hoy: solo la venta 32)
-- ============================================================

BEGIN;

UPDATE public.venta
   SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
                           WHERE nombre = 'confirmado')
 WHERE id_estado_venta = (SELECT id_estado_venta FROM estado_venta
                           WHERE nombre = 'pendiente')
   AND EXISTS (SELECT 1 FROM public.pago p
                WHERE p.id_venta = venta.id_venta
                  AND p.estado = 'confirmado');

COMMIT;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   El PRE-CHEQUEO de arriba debe devolver 0 filas.
--   SELECT v.id_venta, ev.nombre AS estado, p.estado AS pago
--     FROM venta v JOIN pago p ON p.id_venta = v.id_venta
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE v.id_venta IN (SELECT id_registro FROM auditoria
--                          WHERE tabla_afectada = 'venta'
--                            AND valor_nuevo = 'confirmado'
--                            AND accion LIKE '%estado%');
--   SELECT COUNT(*) FROM auditoria;   -- +1 por cada venta corregida
--
-- REVERSION (comentada; ejecutar solo si se decide revertir).
--   Hoy la única afectada es la venta 32 (ajustar la lista según el
--   PRE-CHEQUEO que hayas ejecutado):
--   UPDATE public.venta
--      SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
--                              WHERE nombre = 'pendiente')
--    WHERE id_venta IN (32);
--   -- El trigger registrará también esta reversión en auditoria.
-- ============================================================
