-- ============================================================
-- CLEOFERR - CORRECCION: ventas CONFIRMADAS con pago PENDIENTE
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO fue ejecutado por el asistente: redactado 2026-10-12.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo: alinear el estado de la venta con su pago. 'confirmado'
--   SOLO debe provenir de confirmar_pago (pago confirmado). Hay ventas
--   movidas MANUALMENTE a 'confirmado' (dropdown del panel o SQL)
--   con su pago todavia pendiente.
--
-- DIAGNOSTICO VERIFICADO EL 2026-10-12 (solo lectura):
--   * La venta 223 YA fue cancelada hoy por el propietario
--     (auditoria: 1->6 confirmado a las 02:25 UTC, 6->5 cancelado a
--     las 17:25 UTC): este script no la toca (solo apunta a estado
--     'confirmado').
--   * Ventas en 'confirmado' con pago 'pendiente': 92 y 93 (online,
--     2026-10-04, pagos legados 57/58 SIN id_transaccion y monto
--     NULL; comprobantes legados B000051/B000052 'no_aplica').
--   * CAUSA: cambio de estado manual (cambio_estado 1->6 registrado
--     en auditoria, id_usuario NULL) sin pasar por confirmar_pago.
--
-- DECISION (propietario): revertir a 'pendiente' (opcion A, minima):
--   la venta vuelve a esperar su pago. NOTA: los pagos legados 57/58
--   no tienen id_transaccion, por lo que NO se pueden pagar por la
--   app actual; si prefieres cerrarlas definitivamente, la opcion B
--   (cancelar venta+pago) va comentada al final.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT v.id_venta, ev.nombre AS estado, p.id_pago, p.estado AS pago
--     FROM venta v
--     JOIN pago p ON p.id_venta = v.id_venta
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE ev.nombre = 'confirmado'
--      AND p.estado <> 'confirmado';
--     -- lista de afectados (hoy: 92 y 93)
--   SELECT v.id_venta, c.numero, c.estado_sunat
--     FROM venta v JOIN comprobante c ON c.id_venta = v.id_venta
--     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
--    WHERE ev.nombre = 'confirmado'
--      AND NOT EXISTS (SELECT 1 FROM pago p WHERE p.id_venta = v.id_venta
--                       AND p.estado = 'confirmado');
--     -- comprobantes legados que quedarian en ventas pendientes
--     -- (hoy B000051/B000052 'no_aplica': inertes, ver NOTA al final)
-- ============================================================

BEGIN;

-- Solo ventas CONFIRMADAS cuyo pago NO este confirmado (uq_pago_venta
-- garantiza un unico pago por venta). El trigger trg_auditar_estado_venta
-- registra el cambio en auditoria.
UPDATE public.venta
   SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
                           WHERE nombre = 'pendiente')
 WHERE id_estado_venta = (SELECT id_estado_venta FROM estado_venta
                           WHERE nombre = 'confirmado')
   AND NOT EXISTS (SELECT 1 FROM public.pago p
                    WHERE p.id_venta = venta.id_venta
                      AND p.estado = 'confirmado');

COMMIT;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   El primer PRE-CHEQUEO debe devolver 0 filas.
--   SELECT COUNT(*) FROM auditoria;  -- +1 por cada venta corregida
--
-- NOTA comprobantes legados: B000051/B000052 (estado_sunat 'no_aplica',
--   total NULL) son del flujo anterior a la fase 2.2. Al volver las
--   ventas a 'pendiente' quedan inertes. Si alguna de esas ventas se
--   pagara algun dia por la app, la emision D1 es idempotente por
--   venta y devolveria la fila legada sin totales: en ese caso se
--   decide aparte si depurar esas filas (decision del propietario).
--
-- OPCION B (comentada; solo si prefieres cerrarlas definitivamente).
--   Cancelar venta y pago de las afectadas (ajustar la lista al
--   PRE-CHEQUEO). OJO: el stock ya fue descontado en su momento y NO
--   se devuelve en esta opcion rapida:
--   BEGIN;
--   UPDATE public.pago SET estado = 'cancelado'
--    WHERE id_pago IN (57, 58) AND estado = 'pendiente';
--   UPDATE public.venta
--      SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
--                              WHERE nombre = 'cancelado')
--    WHERE id_venta IN (92, 93)
--      AND id_estado_venta = (SELECT id_estado_venta FROM estado_venta
--                              WHERE nombre = 'confirmado');
--   COMMIT;
--
-- REVERSION (comentada; ejecutar solo si se decide revertir la
--   opcion A, ajustando la lista al resultado del PRE-CHEQUEO):
--   UPDATE public.venta
--      SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
--                              WHERE nombre = 'confirmado')
--    WHERE id_venta IN (92, 93)
--      AND id_estado_venta = (SELECT id_estado_venta FROM estado_venta
--                              WHERE nombre = 'pendiente');
-- ============================================================
