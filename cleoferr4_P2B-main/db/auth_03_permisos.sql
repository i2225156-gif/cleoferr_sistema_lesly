-- ============================================================
-- CLEOFERR - AUTH-03: permisos por módulo para el personal
-- !! EJECUTAR EN EL PROYECTO AUTH, NO EN TIENDA !!
-- BD: AUTH (Supabase PostgreSQL). Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase del proyecto
--          AUTH. NO fue ejecutado por el asistente: redactado 2026-10-28.
-- Idempotente: CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING
--          permiten re-ejecutarlo sin duplicar nada ni fallar.
-- ============================================================
-- Objetivo:
--   Repartir módulos del panel entre el personal (vendedor ya no puede
--   usar «casi todo»; el administrador SIEMPRE tiene todos, eso vive en
--   la aplicación, no aquí):
--
--   Tabla usuario_permiso:
--     * id_usuario: FK a usuario(id_usuario) con ON DELETE CASCADE
--       (al borrar el usuario desaparecen sus permisos; sin huérfanas).
--     * permiso: clave del catálogo (VARCHAR(30)); el CHECK impone el
--       catálogo válido, misma lista que services/permisos.py.
--     * PK (id_usuario, permiso): un módulo una fila por usuario.
--
--   Semilla de permisos por defecto para los vendedores YA existentes
--   (ventas, pedidos, clientes, alertas) = POR_DEFECTO_VENDEDOR:
--     * ON CONFLICT DO NOTHING: si el propietario ajustó permisos a
--       mano y re-ejecuta, no se restauran filas borradas a mano.
--     * ACK (administrador): sin filas por diseño — la app le da todos.
--       (Nota: id_rol por NOMBRE 'vendedor', igual que la app no
--       asume ids numéricos de rol.)
--
--   Catálogo (debe coincidir con services/permisos.py):
--     ventas, pedidos, clientes, productos, inventario, proveedores,
--     ofertas, reportes, alertas, compras.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT id_rol, nombre FROM rol;
--     -> ver que existe 'vendedor' (el catálogo de rol lo maneja la BD).
--   SELECT * FROM usuario_permiso LIMIT 5;  (si falla: tabla nueva)
-- ============================================================

BEGIN;

-- 1) Tabla de permisos: PK compuesta, FK cascada y catálogo cerrado.
CREATE TABLE IF NOT EXISTS usuario_permiso (
    id_usuario INTEGER NOT NULL,
    permiso    VARCHAR(30) NOT NULL,

    CONSTRAINT usuario_permiso_pk PRIMARY KEY (id_usuario, permiso),

    CONSTRAINT usuario_permiso_usuario_fk FOREIGN KEY (id_usuario)
        REFERENCES usuario(id_usuario) ON DELETE CASCADE,

    -- Catálogo de módulos (misma lista que services/permisos.py CATALOGO)
    CONSTRAINT usuario_permiso_catalogo_chk CHECK (permiso IN (
        'ventas', 'pedidos', 'clientes', 'productos', 'inventario',
        'proveedores', 'ofertas', 'reportes', 'alertas', 'compras'))
);

-- 2) Permisos por defecto para los vendedores ya existentes.
--    CROSS JOIN usuario x los 4 módulos por defecto; ON CONFLICT DO
--    NOTHING para idempotencia (re-ejecutar no duplica).
INSERT INTO usuario_permiso (id_usuario, permiso)
SELECT u.id_usuario, d.permiso
  FROM usuario u
  INNER JOIN rol r ON r.id_rol = u.id_rol
 CROSS JOIN (VALUES
       ('ventas'),
       ('pedidos'),
       ('clientes'),
       ('alertas')
     ) AS d(permiso)
 WHERE LOWER(TRIM(r.nombre)) = 'vendedor'
ON CONFLICT (id_usuario, permiso) DO NOTHING;

COMMIT;

-- POST-CHEQUEOS (ejecutar DESPUÉS; solo lectura):
--   SELECT COUNT(*) FROM usuario_permiso;
--     -> 4 filas por cada vendedor existente.
--   SELECT p.id_usuario, COUNT(*) FROM usuario_permiso p
--     GROUP BY p.id_usuario ORDER BY p.id_usuario;
--     -> cada vendedor con sus 4 permisos por defecto.
--   SELECT conname, contype
--     FROM pg_constraint WHERE conrelid = 'usuario_permiso'::regclass;
--     -> PK (usuario_permiso_pk), FK (usuario_permiso_usuario_fk) y CHECK
--        (usuario_permiso_catalogo_chk) creados.
