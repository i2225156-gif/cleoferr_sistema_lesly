-- ============================================================
-- CLEOFERR - AUTH-02: añadir Carné de Extranjería (CE) al catálogo
-- !! EJECUTAR EN EL PROYECTO AUTH, NO EN TIENDA !!
-- BD: AUTH (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase del proyecto
--          AUTH (ref wozycehfpjghqznsuztv).
--          NO fue ejecutado por el asistente: redactado 2026-10-12.
-- Idempotente: puede re-ejecutarse sin duplicar nada.
-- ============================================================
-- Objetivo:
--   Añadir 'CE' (Carné de Extranjería) al catálogo tipo_documento
--   para que el registro de clientes pueda ofrecerlo como opción.
--
--   El código de la aplicación BUSCA EL TIPO POR NOMBRE (no por id),
--   así que el id que asigne la BD es irrelevante para la app.
--
--   NOTA: el FORMATO del CE (longitud, caracteres permitidos) está
--   marcado "A VERIFICAR" en el código (services/documentos.py) y
--   en docs/UMBRAL_BOLETA_SIN_DOC.md; no se inventa aquí.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT * FROM tipo_documento ORDER BY id_tipo_documento;
--     -> ver el catálogo actual y que 'CE' no existe aún
--   SELECT COUNT(*) FROM tipo_documento WHERE LOWER(nombre) = 'ce';
--     -> 0 esperado (si da 1, el script no hará nada: idempotente)
--
-- VERIFICACIONES PREVIAS HECHAS EL 2026-10-12 (solo lectura):
--   * tipo_documento tiene 4 filas: 1=DNI, 2=RUC, 3=Pasaporte, 4=Otro.
--   * 'CE' no existe en el catálogo.
--   * La columna nombre no tiene UNIQUE explícito (el INSERT usa
--     WHERE NOT EXISTS por si se re-ejecuta o ya fue añadido a mano).
-- ============================================================

BEGIN;

-- 1) Insertar 'CE' por NOMBRE, sin id explícito (la BD asigna el id).
INSERT INTO tipo_documento (nombre)
SELECT 'CE'
 WHERE NOT EXISTS (
     SELECT 1 FROM tipo_documento WHERE LOWER(TRIM(nombre)) = 'ce'
 );

COMMIT;

-- ============================================================
-- VERIFICACION POSTERIOR (ejecutar tras este script; lectura):
--   SELECT * FROM tipo_documento ORDER BY id_tipo_documento;
--     -> debe listar 5 filas, incluyendo 'CE'
--   SELECT COUNT(*) FROM tipo_documento WHERE LOWER(TRIM(nombre)) = 'ce';
--     -> 1 esperado
--
-- REVERSION (comentada; ejecutar solo si se decide revertir):
--   DELETE FROM tipo_documento
--    WHERE LOWER(TRIM(nombre)) = 'ce'
--      AND NOT EXISTS (
--          SELECT 1 FROM cliente c
--           WHERE c.id_tipo_documento = tipo_documento.id_tipo_documento
--      );
--   -- La condición NOT EXISTS impide borrar el tipo si ya hay clientes
--   -- que lo usan. Si hay clientes con CE, primero hay que cambiarles
--   -- el tipo de documento.
-- ============================================================
