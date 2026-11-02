-- ============================================================
-- CLEOFERR - AUTH-03: permisos por módulo para el personal
-- !! EJECUTAR EN EL PROYECTO AUTH, NO EN TIENDA !!
-- BD: AUTH (Supabase PostgreSQL, proyecto login_cleoferr).
-- Dialecto PostgreSQL puro.
-- EJECUTA: el propietario en el SQL Editor de Supabase del proyecto
--          AUTH. NO fue ejecutado por el asistente: redactado
--          2026-10-28 contra la estructura VERIFICADA en
--          information_schema (tablas usuario_permiso, permiso y
--          rol_permiso YA EXISTEN y están vacías; NO se crea ninguna).
-- Idempotente: NOT EXISTS garantiza re-ejecuciones sin duplicados
--          ni errores.
-- ============================================================
-- Estructura REAL verificada (information_schema, 2026-10-28):
--   permiso(id_permiso BIGINT serial PK, nombre VARCHAR(80) NOT NULL
--           UNIQUE (permiso_nombre_key), activo BOOLEAN NOT NULL
--           DEFAULT true)
--   rol_permiso(id_rol, id_permiso PK compuesta; FK CASCADE a rol y
--           permiso)
--   usuario_permiso(id_usuario BIGINT NOT NULL PK+FK usuario ON DELETE
--           CASCADE, id_permiso BIGINT NOT NULL PK+FK permiso ON
--           DELETE CASCADE, asignado_por BIGINT NULL FK usuario
--           ON DELETE NO ACTION, fecha_asignacion TIMESTAMPTZ NOT NULL
--           DEFAULT now())
--   rol contiene: 'administrador' y 'vendedor'.
--
-- AUDITORÍA (contrato para el panel del administrador):
--   * DAR permiso      -> INSERT en usuario_permiso con
--                         asignado_por = id del ADMINISTRADOR de la
--                         sesión y fecha_asignacion = now().
--   * QUITAR permiso   -> DELETE de la fila (borrado directo; sin
--                         historial por diseño).
--   * La siembra inicial usa asignado_por = NULL porque la columna
--     ADMITE NULL (verificado): los permisos por defecto son de la
--     plantilla del rol, no de ninguna persona.
--     Si la regla cambiase a NOT NULL, sustituye 'NULL' por:
--       (SELECT uu.id_usuario FROM usuario uu
--         JOIN rol rr ON rr.id_rol = uu.id_rol
--        WHERE LOWER(TRIM(rr.nombre)) = 'administrador' AND uu.activo
--        ORDER BY uu.id_usuario LIMIT 1)
--
-- Catálogo (debe coincidir con services/permisos.py CATALOGO):
--   ventas, pedidos, clientes, productos, inventario, proveedores,
--   ofertas, reportes, alertas, compras.
--
-- ORDEN IMPORTANTE: 1) permiso -> 2) rol_permiso -> 3) usuario_permiso.
--
-- PRE-CHEQUEOS (ejecutar ANTES; solo lectura):
--   SELECT * FROM permiso;            -> 0 filas (vacía, verificado)
--   SELECT * FROM rol_permiso;        -> 0 filas (vacía, verificado)
--   SELECT * FROM usuario_permiso;    -> 0 filas (vacía, verificado)
--   SELECT DISTINCT LOWER(TRIM(nombre)) FROM rol ORDER BY 1;
--     -> {'administrador','vendedor'} (verificado)
-- ============================================================

BEGIN;

-- 1) Catálogo de permisos: SIEMBRA DE permiso (nombre UNIQUE +
--    activo = TRUE). NOT EXISTS → idempotente aunque ya existiera
--    alguna fila o variante de mayúsculas.
INSERT INTO permiso (nombre, activo)
SELECT d.nombre, TRUE
  FROM (VALUES
          ('ventas'),
          ('pedidos'),
          ('clientes'),
          ('productos'),
          ('inventario'),
          ('proveedores'),
          ('ofertas'),
          ('reportes'),
          ('alertas'),
          ('compras')
        ) AS d(nombre)
 WHERE NOT EXISTS (
       SELECT 1 FROM permiso p
        WHERE LOWER(TRIM(p.nombre)) = LOWER(TRIM(d.nombre)));

-- 2) rol_permiso: plantilla del ROL vendedor (el orden garantiza que
--    el catálogo ya existe). NOT EXISTS → idempotente.
INSERT INTO rol_permiso (id_rol, id_permiso)
SELECT r.id_rol, p.id_permiso
  FROM rol r
 CROSS JOIN (VALUES
       ('ventas'),
       ('pedidos'),
       ('clientes'),
       ('alertas')
     ) AS d(nombre)
  JOIN permiso p ON LOWER(TRIM(p.nombre)) = d.nombre
 WHERE LOWER(TRIM(r.nombre)) = 'vendedor'
   AND NOT EXISTS (
       SELECT 1 FROM rol_permiso rp
        WHERE rp.id_rol = r.id_rol AND rp.id_permiso = p.id_permiso);

-- 3) usuario_permiso: cada VENDEDOR EXISTENTE copia la plantilla de
--    su rol (rol_permiso). asignado_por = NULL (auditable: sembrado
--    por defecto, sin persona responsable) y fecha_asignacion =
--    now(). NOT EXISTS → idempotente y NO reemplaza permisos dados a
--    mano; re-ejecutar tras el panel solo reintroduciría los de la
--    plantilla del rol.
INSERT INTO usuario_permiso
       (id_usuario, id_permiso, asignado_por, fecha_asignacion)
SELECT u.id_usuario, p.id_permiso, NULL, now()
  FROM usuario u
  JOIN rol r ON r.id_rol = u.id_rol
  JOIN rol_permiso rp ON rp.id_rol = r.id_rol
  JOIN permiso p ON p.id_permiso = rp.id_permiso AND p.activo = TRUE
 WHERE LOWER(TRIM(r.nombre)) = 'vendedor'
   AND NOT EXISTS (
       SELECT 1 FROM usuario_permiso up
        WHERE up.id_usuario = u.id_usuario
          AND up.id_permiso = p.id_permiso);

COMMIT;

-- POST-CHEQUEOS (ejecutar DESPUÉS; solo lectura):
--   SELECT nombre, activo FROM permiso ORDER BY id_permiso;
--     -> 10 filas activas: el catálogo completo.
--   SELECT p.nombre AS permiso
--     FROM rol_permiso rp
--     JOIN rol r ON r.id_rol = rp.id_rol
--     JOIN permiso p ON p.id_permiso = rp.id_permiso
--    WHERE LOWER(TRIM(r.nombre)) = 'vendedor'
--    ORDER BY p.nombre;
--     -> 4 filas: ventas, pedidos, clientes, alertas.
--   SELECT up.id_usuario, p.nombre AS permiso, up.asignado_por,
--          up.fecha_asignacion
--     FROM usuario_permiso up
--     JOIN usuario u ON u.id_usuario = up.id_usuario
--     JOIN rol r ON r.id_rol = u.id_rol
--     JOIN permiso p ON p.id_permiso = up.id_permiso
--    WHERE LOWER(TRIM(r.nombre)) = 'vendedor';
--     -> 4 filas POR VENDEDOR con asignado_por NULL.
