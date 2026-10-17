-- ============================================================
-- CLEOFERR - FASE 2.1 TRAMO 2 (paso 2a): soporte de pasarela en TIENDA
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
--          NO fue ejecutado por el asistente: redactado 2026-10-11.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo (decisiones D3/D4/D7 aprobadas):
--   * pago.estado admite 'cancelado' y 'reembolsado' (D4). D3 reintenta
--     sobre la MISMA fila (rechazado->pendiente), sin INSERTs nuevos.
--     'desconocido' NUNCA se escribe en pago.estado (solo existe en
--     memoria y, como constancia, en pago_evento.estado_resultante).
--   * pago.detalle_proveedor jsonb: metadatos NEUTRALES del proveedor.
--     JAMAS datos de tarjeta, credenciales ni secretos (regla A.3).
--   * pago_evento: bitacora de eventos del webhook con
--     UNIQUE(proveedor, id_evento_proveedor) -> idempotencia por id de
--     evento (un reenvio no duplica efectos). Guarda tambien los
--     eventos RECHAZADOS (firma invalida / fuera de tolerancia) con
--     payload redactado.
--
-- REGLA 2b: confirmar_pago sera la UNICA puerta del codigo para pasar
--   un pago a 'confirmado'; requiere ESTE script ejecutado antes.
--
-- DEPENDENCIAS: db/02_pago_transaccion.sql (tabla pago + uq_pago_venta),
--   ya ejecutado y verificado.
--
-- VERIFICACIONES PREVIAS HECHAS EL 2026-10-11 (solo lectura):
--   * CHECK actual: estado IN ('pendiente','confirmado','rechazado').
--   * Filas actuales: 16 pendiente / 8 confirmado; 0 filas usan valores
--     fuera del CHECK actual -> ampliarlo no puede violar datos.
--   * pago.detalle_proveedor no existe; pago_evento no existe; sin
--     colisiones de nombres (uq_pago_evento_proveedor,
--     chk_pago_evento_estado, idx_pago_evento_*); pago.id_pago existe
--     (FK posible).
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conname = 'pago_estado_check';          -- def. actual (3 valores)
--   SELECT estado, COUNT(*) FROM pago GROUP BY estado;
--     -> solo valores del CHECK actual
--   SELECT COUNT(*) FROM pago
--     WHERE estado NOT IN ('pendiente','confirmado','rechazado');  -- 0
--   SELECT to_regclass('public.pago_evento');       -- NULL esperado
--   SELECT COUNT(*) FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='pago'
--       AND column_name='detalle_proveedor';        -- 0 esperado
-- ============================================================

BEGIN;

-- 1) Metadatos neutrales del proveedor en pago (jsonb, nullable: las
--    filas existentes quedan en NULL; el codigo escribira '{}' + claves
--    neutrales, nunca datos sensibles).
ALTER TABLE public.pago
    ADD COLUMN IF NOT EXISTS detalle_proveedor JSONB;

-- 2) Ampliar el CHECK de pago.estado (D4/D7). Idempotente y robusto:
--    se reemplaza solo si no incluye 'cancelado' (o si fue borrado);
--    el pre-chequeo garantiza que ninguna fila viola el nuevo CHECK.
DO $chk$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'pago_estado_check'
                  AND pg_get_constraintdef(oid) NOT LIKE '%cancelado%')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'pago_estado_check')
    THEN
        ALTER TABLE public.pago DROP CONSTRAINT IF EXISTS pago_estado_check;
        ALTER TABLE public.pago ADD CONSTRAINT pago_estado_check
            CHECK (estado IN ('pendiente', 'confirmado', 'rechazado',
                             'cancelado', 'reembolsado'));
    END IF;
END
$chk$;

-- 3) Bitacora de eventos del webhook.
CREATE TABLE IF NOT EXISTS public.pago_evento (
    id_pago_evento       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proveedor            VARCHAR(20)  NOT NULL,
    id_evento_proveedor  VARCHAR(100) NOT NULL,
    tipo                 VARCHAR(50),
    id_pago              BIGINT REFERENCES public.pago(id_pago) ON DELETE SET NULL,
    estado_resultante    VARCHAR(20),
    firma_valida         BOOLEAN      NOT NULL DEFAULT FALSE,
    dentro_de_tolerancia BOOLEAN      NOT NULL DEFAULT TRUE,
    recibido_en          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    procesado_en         TIMESTAMPTZ,
    detalle              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_pago_evento_proveedor UNIQUE (proveedor, id_evento_proveedor),
    CONSTRAINT chk_pago_evento_estado CHECK (
        estado_resultante IS NULL OR estado_resultante IN
            ('pendiente', 'confirmado', 'rechazado', 'cancelado',
             'reembolsado', 'desconocido')
    )
);

-- 4) Indices: busqueda por pago (seguimiento de un pago) y por fecha
--    (panel de demo: ultimos eventos).
CREATE INDEX IF NOT EXISTS idx_pago_evento_id_pago
    ON public.pago_evento (id_pago);
CREATE INDEX IF NOT EXISTS idx_pago_evento_recibido
    ON public.pago_evento (recibido_en DESC);

-- 5) RLS: activo y sin politicas, mismo criterio que el resto de TIENDA
--    (solo conecta el backend con su rol de servicio).
ALTER TABLE public.pago_evento ENABLE ROW LEVEL SECURITY;

COMMIT;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--     WHERE conname = 'pago_estado_check';
--     -> debe listar los 5 estados
--   SELECT COUNT(*) FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='pago'
--       AND column_name='detalle_proveedor';               -- 1
--   SELECT conname FROM pg_constraint
--     WHERE conrelid = 'public.pago_evento'::regclass ORDER BY 1;
--     -> pago_evento_pkey, uq_pago_evento_proveedor,
--        chk_pago_evento_estado, FK a pago
--   SELECT indexname FROM pg_indexes
--     WHERE schemaname='public' AND tablename='pago_evento';
--   SELECT relrowsecurity FROM pg_class
--     WHERE oid = 'public.pago_evento'::regclass;          -- true
--
-- PRUEBA EN VIVO de la idempotencia por evento (transaccion desechable;
-- copiar en el editor: el SEGUNDO INSERT debe fallar y el ROLLBACK
-- revertir todo):
--   BEGIN;
--   INSERT INTO pago_evento (proveedor, id_evento_proveedor, tipo,
--                            estado_resultante, firma_valida)
--   VALUES ('simulada', 'evt-prueba-1', 'pago_actualizado', 'confirmado', TRUE);
--   INSERT INTO pago_evento (proveedor, id_evento_proveedor, tipo,
--                            estado_resultante, firma_valida)
--   VALUES ('simulada', 'evt-prueba-1', 'pago_actualizado', 'confirmado', TRUE);
--   -- ERROR esperado: duplicate key value violates unique constraint
--   --                 "uq_pago_evento_proveedor"
--   ROLLBACK;
--
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   DROP TABLE IF EXISTS public.pago_evento;   -- tabla + indices + FK
--   ALTER TABLE public.pago DROP COLUMN IF EXISTS detalle_proveedor;
--   -- Restaurar el CHECK original SOLO si ninguna fila usa los valores
--   -- nuevos (verificar antes: SELECT DISTINCT estado FROM pago):
--   ALTER TABLE public.pago DROP CONSTRAINT IF EXISTS pago_estado_check;
--   ALTER TABLE public.pago ADD CONSTRAINT pago_estado_check
--       CHECK (estado IN ('pendiente', 'confirmado', 'rechazado'));
-- ============================================================
