-- ============================================================
-- CLEOFERR - AUTH-01: indices de la BD AUTH
-- !! EJECUTAR EN EL PROYECTO AUTH, NO EN TIENDA !!
-- EJECUTA: el propietario en el SQL Editor de Supabase del proyecto
--          AUTH (conectandose a esa BD).
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   1) Indice funcional del login: la aplicacion consulta
--      WHERE LOWER(TRIM(u.email)) = %s (app.py:281). El UNIQUE(email)
--      existente (usuario_email_key) NO sirve para esa expresion;
--      sin este indice cada login es un seq scan.
--   2) Limpieza de idx_verificacion_cuenta_cliente: REDUNDANTE.
--      Definicion verificada el 2026-10-11:
--        CREATE INDEX idx_verificacion_cuenta_cliente
--            ON verificacion_cuenta USING btree (id_cliente)
--      Queda cubierta por:
--        idx_verif_cuenta_cliente (id_cliente, usado, creado_en DESC)
--          -> (id_cliente) es prefijo izquierdo exacto
--        idx_verificacion_cliente_vigente (id_cliente) WHERE (usado = false)
--      y NO respalda ningun constraint (verificado con pg_constraint:
--      0 filas con conindid = ese indice) -> DROP INDEX directo, sin
--      CASCADE y sin riesgo.
--
-- PRE-CHEQUEOS (ejecutar ANTES, en AUTH):
--   SELECT indexdef FROM pg_indexes
--     WHERE indexname = 'idx_verificacion_cuenta_cliente';   -- def. de arriba
--   SELECT c.conname FROM pg_constraint c
--     JOIN pg_class i ON i.oid = c.conindid
--    WHERE i.relname = 'idx_verificacion_cuenta_cliente';     -- 0 filas esperado
--   SELECT indexdef FROM pg_indexes WHERE schemaname='public'
--     AND tablename='usuario' AND indexdef ILIKE '%lower%';    -- 0 filas (no existia)
-- ============================================================

-- 1) Indice funcional del email normalizado (login).
CREATE INDEX IF NOT EXISTS idx_usuario_email_norm
    ON public.usuario (LOWER(TRIM(email)));

-- 2) Limpieza del indice redundante.
DROP INDEX IF EXISTS public.idx_verificacion_cuenta_cliente;

-- ============================================================
-- VERIFICACION POSTERIOR (en AUTH):
--   SELECT indexdef FROM pg_indexes
--     WHERE schemaname='public' AND tablename='usuario'
--       AND indexname='idx_usuario_email_norm';   -- def. nueva
--   SELECT indexname FROM pg_indexes
--     WHERE indexname='idx_verificacion_cuenta_cliente';  -- 0 filas
--   EXPLAIN SELECT id_usuario FROM usuario
--     WHERE LOWER(TRIM(email)) = 'correo@ejemplo.com';  -- Index Scan esperado
--
-- ROLLBACK (comentado):
--   DROP INDEX IF EXISTS public.idx_usuario_email_norm;
--   CREATE INDEX idx_verificacion_cuenta_cliente
--       ON public.verificacion_cuenta (id_cliente);
-- ============================================================
