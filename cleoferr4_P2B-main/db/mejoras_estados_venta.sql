-- ============================================================
-- CLEOFERR – FASE 1A: alineación del catálogo estado_venta
-- BD: TIENDA (Supabase PostgreSQL)
-- REVISIÓN MANUAL: NO ejecutar sin aprobación del propietario.
-- Idempotente: puede re-ejecutarse sin duplicar filas.
-- ============================================================
-- Flujo objetivo del pedido online (coincide con las plantillas):
--   pendiente → confirmado (pago confirmado) → preparando → listo
--   → entregado ; cancelado = estado terminal alternativo.
--
-- Estado actual verificado con SELECT (5 filas):
--   id=1 pendiente (orden 1) | id=2 procesando (orden 2)
--   id=3 enviado   (orden 3) | id=4 entregado (orden 4)
--   id=5 cancelado  (orden 9)
-- Problema: faltan 'confirmado', 'preparando' y 'listo' — los tres
-- nombres que usan mis_pedidos.html:98 y pedido_aceptado.html:56-104.
-- ============================================================

-- 1) Renombrar fases para que coincidan con las plantillas.
--    (venta.id_estado_venta es FK por ID: el renombre no rompe nada).
UPDATE estado_venta
   SET nombre = 'preparando'
 WHERE nombre = 'procesando'
   AND NOT EXISTS (SELECT 1 FROM estado_venta e WHERE e.nombre = 'preparando');

UPDATE estado_venta
   SET nombre = 'listo'
 WHERE nombre = 'enviado'
   AND NOT EXISTS (SELECT 1 FROM estado_venta e WHERE e.nombre = 'listo');

-- 2) Insertar las fases del catálogo objetivo que falten
--    (hoy insertaría solo 'confirmado'; las demás ya existen tras el paso 1).
INSERT INTO estado_venta (nombre, orden, activo)
SELECT v.nombre, v.orden, TRUE
FROM (VALUES
    ('pendiente',  1),
    ('confirmado', 2),
    ('preparando', 3),
    ('listo',      4),
    ('entregado',  5),
    ('cancelado',  6)
) AS v(nombre, orden)
WHERE NOT EXISTS (SELECT 1 FROM estado_venta e WHERE e.nombre = v.nombre);

-- 3) Normalizar el orden de las filas existentes (idempotente).
--    Hoy: preparando quedó con orden 2, listo con 3, entregado 4,
--    cancelado 9; el objetivo es 1..6.
UPDATE estado_venta e
   SET orden = v.orden
FROM (VALUES
    ('pendiente',  1),
    ('confirmado', 2),
    ('preparando', 3),
    ('listo',      4),
    ('entregado',  5),
    ('cancelado',  6)
) AS v(nombre, orden)
WHERE e.nombre = v.nombre
  AND e.orden IS DISTINCT FROM v.orden;

-- ============================================================
-- NOTAS PARA LA REVISIÓN (no requieren ejecutar nada):
--
-- * Ventas afectadas por el renombre (conteos verificados hoy):
--     1 venta en 'procesando' se mostrará como 'preparando'
--     1 venta en 'enviado'    se mostrará como 'listo'
--     23 'pendiente', 13 'entregado' y 1 'cancelado' no cambian.
-- * pedidos.html / pedido_detalle.html / mis_pedidos.html leen los
--   nombres de esta tabla (app.py:1313, 1963, 2830, 2907) y ya tienen
--   iconos y badges para 'preparando' y 'listo' (mis_pedidos.html:109,
--   111; pedido_detalle.html:89, 91; base.html:207-208), así que las
--   vistas se adaptan solas tras ejecutar este script.
-- * El trigger trg_auditar_estado_venta (AFTER UPDATE ON venta)
--   registrará automáticamente en auditoria cada cambio de estado del
--   nuevo flujo (confirmar / rechazar / cancelar).
-- * La tabla pago NO necesita cambios: el constraint pago_estado_check
--   ya admite 'pendiente', 'confirmado' y 'rechazado' (verificado).
-- * estado_venta.nombre tiene UNIQUE (estado_venta_nombre_key); los
--   WHERE NOT EXISTS evitan colisiones y permiten re-ejecutar esto.
-- ============================================================
