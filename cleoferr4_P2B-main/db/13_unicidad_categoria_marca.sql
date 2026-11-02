-- ============================================================
-- CLEOFERR - Unicidad de nombres de categoría y marca
-- BD: TIENDA (Supabase PostgreSQL).
-- PROPUESTA NO EJECUTADA por el asistente (2026-11-02).
-- El propietario debe revisar y ejecutar manualmente si la aprueba.
-- ============================================================
-- La aplicación ya compara sin distinguir mayúsculas ni tildes dentro
-- de una transacción serializada. Estos índices refuerzan en la BD la
-- unicidad sin distinguir mayúsculas, tal como se definió para este
-- cambio de esquema. La normalización de tildes sigue en la aplicación.

BEGIN;

-- PRE-CHEQUEO OBLIGATORIO: si hay duplicados, el DO detiene el script
-- y la transacción no crea ningún índice. Resolverlos antes de reintentar.
DO $prechequeo$
BEGIN
    IF EXISTS (
        SELECT lower(nombre)
          FROM public.categoria
         GROUP BY lower(nombre)
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Hay categorías duplicadas por lower(nombre); revise y consolide antes de crear el índice.';
    END IF;

    IF EXISTS (
        SELECT lower(nombre)
          FROM public.marca
         GROUP BY lower(nombre)
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Hay marcas duplicadas por lower(nombre); revise y consolide antes de crear el índice.';
    END IF;
END
$prechequeo$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_categoria_nombre_lower
    ON public.categoria (lower(nombre));

CREATE UNIQUE INDEX IF NOT EXISTS uq_marca_nombre_lower
    ON public.marca (lower(nombre));

COMMIT;

-- REVERSIÓN (comentada; ejecutar solo tras revisar el impacto):
-- DROP INDEX IF EXISTS public.uq_categoria_nombre_lower;
-- DROP INDEX IF EXISTS public.uq_marca_nombre_lower;
