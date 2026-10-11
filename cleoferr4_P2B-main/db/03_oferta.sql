-- ============================================================
-- CLEOFERR - B3: catalogo de ofertas por producto
-- BD: TIENDA (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   * oferta: descuentos por producto con vigencia temporal y sin
--     solapamientos entre ofertas ACTIVAS del mismo producto.
--   * detalle_venta referencia la oferta aplicada y guarda el
--     descuento unitario snapshot (0 por defecto, retro-compatible).
--
-- DEPENDENCIAS: ninguna para ejecutar. El codigo que consulte el
--   "precio vigente" (descuentos activos) REQUIERE este script
--   ejecutado (tabla oferta + columnas de detalle_venta).
--
-- VERIFICACIONES PREVIAS HECHAS EL 2026-10-11 (solo lectura):
--   * btree_gist DISPONIBLE (pg_available_extensions, v1.7, no instalada)
--     y el esquema 'extensions' existe -> la exclusion gist resuelve:
--     '=' sobre bigint lo aporta btree_gist; '&&' sobre tstzrange es
--     gist nativo (range_ops, nucleo de PostgreSQL).
--   * to_regclass('public.oferta') IS NULL -> la tabla no existe.
--   * Sin colisiones de nombres: fn_oferta_validar_precio(),
--     ex_oferta_vigencia, chk_oferta_*, chk_detalle_descuento,
--     idx_oferta_* no existen.
--   * Roles anon y authenticated existen (para el REVOKE).
--   * trg_detalle_venta_nombre (fn_detalle_venta_nombre) SOLO llena
--     NEW.nombre_producto desde producto si es NULL: no toca las
--     columnas nuevas -> SIN choque.
--   * detalle_venta.total es GENERATED ALWAYS AS
--     round(cantidad * precio_unitario, 2) STORED: anadir columnas
--     NO la afecta. El descuento NO se resta automatico del total;
--     aplicarlo es decision del codigo futuro.
--   * producto.precio es numeric -> comparable con valor.
--
-- PRE-CHEQUEOS (ejecutar ANTES de este script):
--   SELECT to_regclass('public.oferta');                -- NULL esperado
--   SELECT name, default_version FROM pg_available_extensions
--     WHERE name = 'btree_gist';                        -- 1 fila esperada
--   SELECT column_name FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='detalle_venta'
--       AND column_name IN ('id_oferta','descuento_unitario');  -- 0 filas
-- ============================================================

-- 1) Extension para '='(bigint) dentro de exclusiones gist.
--    (Supabase: se instala en el esquema 'extensions', ya existente.)
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA extensions;

-- 2) Tabla oferta.
--    creado_por es BIGINT suelto: el usuario vive en la BD AUTH
--    (cross-BD: FK imposible, mismo criterio que pago.confirmado_por).
CREATE TABLE IF NOT EXISTS public.oferta (
    id_oferta    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_producto  BIGINT NOT NULL REFERENCES public.producto(id_producto) ON DELETE CASCADE,
    tipo         VARCHAR(11) NOT NULL,
    valor        NUMERIC(12,2) NOT NULL,
    fecha_inicio TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_fin    TIMESTAMPTZ NOT NULL,
    activa       BOOLEAN NOT NULL DEFAULT TRUE,
    creado_por   BIGINT,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_oferta_tipo        CHECK (tipo IN ('porcentaje', 'precio_fijo')),
    CONSTRAINT chk_oferta_valor       CHECK (valor > 0),
    CONSTRAINT chk_oferta_porcentaje   CHECK (tipo <> 'porcentaje' OR valor <= 100),
    CONSTRAINT chk_oferta_fechas      CHECK (fecha_fin > fecha_inicio)
);

-- 3) Trigger: un precio_fijo nunca puede ser >= al precio del producto.
--    SET search_path = public contra secuestro de objetos; solo el
--    rol de servicio del backend debe poder ejecutarla.
CREATE OR REPLACE FUNCTION public.fn_oferta_validar_precio()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $fn$
DECLARE
    precio_producto NUMERIC(12,2);
BEGIN
    IF NEW.tipo = 'precio_fijo' THEN
        SELECT p.precio INTO precio_producto
          FROM public.producto p
         WHERE p.id_producto = NEW.id_producto;
        IF precio_producto IS NOT NULL AND NEW.valor >= precio_producto THEN
            RAISE EXCEPTION
                'oferta invalida: precio_fijo (%) no puede ser >= al precio del producto (%)',
                NEW.valor, precio_producto;
        END IF;
    END IF;
    RETURN NEW;
END
$fn$;

REVOKE EXECUTE ON FUNCTION public.fn_oferta_validar_precio()
    FROM PUBLIC, anon, authenticated;

DO $tg$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_oferta_validar_precio'
                      AND tgrelid = 'public.oferta'::regclass) THEN
        CREATE TRIGGER trg_oferta_validar_precio
            BEFORE INSERT OR UPDATE ON public.oferta
            FOR EACH ROW EXECUTE FUNCTION public.fn_oferta_validar_precio();
    END IF;
END
$tg$;

-- 4) Exclusion gist: sin vigencias solapadas para ofertas ACTIVAS
--    del mismo producto. Verificado el 2026-10-11 que btree_gist esta
--    disponible y resuelve '='(bigint); '&&'(tstzrange) es gist nativo.
DO $ex$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ex_oferta_vigencia') THEN
        ALTER TABLE public.oferta
            ADD CONSTRAINT ex_oferta_vigencia
            EXCLUDE USING gist (
                id_producto WITH =,
                tstzrange(fecha_inicio, fecha_fin) WITH &&
            ) WHERE (activa);
    END IF;
END
$ex$;

-- ALTERNATIVA (usar SOLO si el paso 1 fallara por permisos de la
-- extension; es MAS estricta: una sola oferta activa por producto):
--   Comentar el bloque anterior y descomentar:
--   DO $alt$
--   BEGIN
--       IF NOT EXISTS (SELECT 1 FROM pg_constraint
--                       WHERE conname = 'uq_oferta_producto_activa') THEN
--           ALTER TABLE public.oferta
--               ADD CONSTRAINT uq_oferta_producto_activa
--               UNIQUE (id_producto) WHERE (activa);
--       END IF;
--   END
--   $alt$;
--   (La validacion de rangos queda en el codigo de la app con
--    SELECT ... FOR UPDATE sobre las ofertas activas del producto.)

-- 5) Indices de consulta (btree: mas baratos que el gist para igualdad).
CREATE INDEX IF NOT EXISTS idx_oferta_producto_activa
    ON public.oferta (id_producto) WHERE (activa);
CREATE INDEX IF NOT EXISTS idx_oferta_fecha_fin_activa
    ON public.oferta (fecha_fin) WHERE (activa);

-- 6) RLS: activo y sin politicas, igual criterio que el resto de TIENDA
--    (solo conecta el backend con su rol; anon/authenticated no leen).
ALTER TABLE public.oferta ENABLE ROW LEVEL SECURITY;

-- 7) detalle_venta: snapshot de la oferta aplicada y descuento unitario.
--    Columnas nuevas con DEFAULT 0: ningun dato actual puede violar
--    el NOT NULL ni el CHECK (pre-chequeo: 0 filas las usan hoy).
ALTER TABLE public.detalle_venta
    ADD COLUMN IF NOT EXISTS id_oferta BIGINT
        REFERENCES public.oferta(id_oferta) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS descuento_unitario NUMERIC(12,2) NOT NULL DEFAULT 0;

DO $ck$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_detalle_descuento') THEN
        ALTER TABLE public.detalle_venta
            ADD CONSTRAINT chk_detalle_descuento
            CHECK (descuento_unitario >= 0);
    END IF;
END
$ck$;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script):
--   SELECT to_regclass('public.oferta');                        -- no NULL
--   SELECT conname FROM pg_constraint
--     WHERE conrelid = 'public.oferta'::regclass ORDER BY 1;
--     -> ex_oferta_vigencia, chk_oferta_*, oferta_pkey, FK a producto
--   SELECT tgname FROM pg_trigger
--     WHERE tgrelid = 'public.oferta'::regclass AND NOT tgisinternal;
--     -> trg_oferta_validar_precio
--   SELECT column_name FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='detalle_venta'
--       AND column_name IN ('id_oferta','descuento_unitario');   -- 2 filas
--   SELECT indexname FROM pg_indexes WHERE tablename='oferta';   -- los 3
--
-- PRUEBA EN VIVO del trigger y de la exclusion (transaccion desechable;
-- copiar tal cual: debe FALLAR y revertir):
--   BEGIN;
--   INSERT INTO oferta (id_producto, tipo, valor, fecha_fin) VALUES
--     ((SELECT MIN(id_producto) FROM producto), 'porcentaje', 10, now() + interval '10 days');
--   INSERT INTO oferta (id_producto, tipo, valor, fecha_fin) VALUES
--     ((SELECT MIN(id_producto) FROM producto), 'porcentaje', 20, now() + interval '5 days');
--   -- ERROR esperado: conflicting key value violates exclusion
--   -- constraint "ex_oferta_vigencia" (solapan y ambas activas)
--   ROLLBACK;
--
-- ROLLBACK (comentado; ejecutar solo si se decide revertir):
--   DROP TRIGGER IF EXISTS trg_oferta_validar_precio ON public.oferta;
--   DROP FUNCTION IF EXISTS public.fn_oferta_validar_precio();
--   ALTER TABLE public.detalle_venta DROP CONSTRAINT IF EXISTS chk_detalle_descuento;
--   ALTER TABLE public.detalle_venta DROP COLUMN IF EXISTS id_oferta;
--   ALTER TABLE public.detalle_venta DROP COLUMN IF EXISTS descuento_unitario;
--   ALTER TABLE public.oferta DROP CONSTRAINT IF EXISTS ex_oferta_vigencia;
--   DROP INDEX IF EXISTS idx_oferta_producto_activa;
--   DROP INDEX IF EXISTS idx_oferta_fecha_fin_activa;
--   DROP TABLE IF EXISTS public.oferta;
--   NOTA: NO se hace DROP EXTENSION btree_gist: la extension es
--   compartida por la BD y desinstalarla podria afectar otros
--   objetos o consultas gist. Tras el ROLLBACK queda instalada pero
--   sin objetos de este script usandola (es inerte e inofensiva).
-- ============================================================
