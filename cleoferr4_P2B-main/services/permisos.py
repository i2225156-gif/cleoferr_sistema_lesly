"""Catálogo de permisos por módulo del personal (TAREA permisos vendedores).

El ADMINISTRADOR tiene SIEMPRE todos los módulos (se impone en la
aplicación, no hay filas para él). El VENDEDOR usa lo registrado en
usuario_permiso de la BD Auth; si NO TIENE NINGUNA fila (tablas vacías
antes de aplicar auth_03_permisos.sql), se aplican POR_DEFECTO_VENDEDOR
y si la propia tabla no existe se registra el error y también cae a los
por defecto.

Estructura REAL verificada con information_schema (proyecto AUTH):
  permiso(id_permiso, nombre UNIQUE, activo)         — id_permiso serial
  rol_permiso(id_rol, id_permiso)                    — plantilla del rol
  usuario_permiso(id_usuario, id_permiso,            — asignado a mano
                  asignado_por, fecha_asignacion)
Las claves de CATALOGO son los NOMBRES de permiso (no ids): la app
se une por permiso.nombre. 'compras' existe en el catálogo para el
módulo futuro: hoy no tiene ninguna ruta asignada.

Auditoría (para el panel del administrador, tarea siguiente):
  * DAR un permiso = INSERT con asignado_por = id del administrador de
    la sesión y fecha_asignacion = now()  ->  dar_permiso().
  * QUITAR un permiso = DELETE de la fila      ->  quitar_permiso().
Ambos son helpers SIN pantallas (reciben la conexión; el que llama
hace commit); los paneles los usarán en la gestión de usuarios.
"""

# Clave de módulo → etiqueta visible en gestión/auditoría.
CATALOGO = {
    'ventas': 'Ventas',
    'pedidos': 'Pedidos',
    'clientes': 'Clientes',
    'productos': 'Productos',
    'inventario': 'Inventario',
    'proveedores': 'Proveedores',
    'ofertas': 'Ofertas',
    'reportes': 'Reportes',
    'alertas': 'Alertas',
    'compras': 'Compras',
}

# Semilla / plantilla del rol vendedor (auth_03_permisos.sql siembra
# rol_permiso con estos cuatro): vendedor SIN filas arranca con ellos.
POR_DEFECTO_VENDEDOR = ('ventas', 'pedidos', 'clientes', 'alertas')


# ── Helpers de gestión (SIN pantallas; el panel los llama) ────────
def dar_permiso(conn, id_admin, id_usuario, clave):
    """Concede un permiso con auditoría: asignado_por + fecha_asignacion.

    Inserta SOLO si la clave está activa en permiso (nombre). Idempotente:
    ON CONFLICT (id_usuario, id_permiso) DO NOTHING no duplica. NO comitea
    (el llamador dueño de la transacción decide). Devuelve True si se
    insertó una fila nueva.
    """
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        INSERT INTO usuario_permiso
               (id_usuario, id_permiso, asignado_por, fecha_asignacion)
        SELECT %s, p.id_permiso, %s, now()
          FROM permiso p
         WHERE p.nombre = %s AND p.activo = TRUE
      ON CONFLICT (id_usuario, id_permiso) DO NOTHING
    """, (id_usuario, id_admin, clave))
    return bool(cursor.rowcount)


def quitar_permiso(conn, id_usuario, clave):
    """Revoca un permiso: el borrado de la fila ES la revocación.

    NO comitea (el llamador decide). Devuelve True si se borró algo.
    """
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        DELETE FROM usuario_permiso up
         USING permiso p
         WHERE up.id_permiso = p.id_permiso
           AND up.id_usuario = %s
           AND p.nombre = %s
    """, (id_usuario, clave))
    return bool(cursor.rowcount)

# ── Tabla ruta → permiso (documentado y verificado por tests). ──────
# Claves: reglas EXACTAS como aparecen en app.url_map. admin_required
# (dashboard, pagos simulados, reservas, eliminaciones) NO está aquí y no
# cambia; ni /api/consultar_ruc ni /api/consultar_dni (las usan también
# los clientes en el carrito).
RUTAS_POR_PERMISO = {
    # productos: listado (agregado ahora), detalle y todo su CRUD
    '/productos':                      'productos',
    '/productos/detalle/<int:id>':     'productos',
    '/productos/nuevo':                'productos',
    '/productos/guardar':              'productos',
    '/productos/editar/<int:id>':      'productos',
    '/productos/actualizar/<int:id>':  'productos',
    '/productos/desactivar/<int:id>':  'productos',
    # clientes: listado, alta y edición (baja sigue solo-admin)
    '/clientes':                       'clientes',
    '/clientes/nuevo':                 'clientes',
    '/clientes/editar/<int:id>':       'clientes',
    # proveedores
    '/proveedores':                    'proveedores',
    '/proveedores/nuevo':              'proveedores',
    '/proveedores/editar/<int:id>':    'proveedores',
    # inventario
    '/inventario':                     'inventario',
    '/inventario/registrar':           'inventario',
    # ventas: POS, alta de cliente en POS, caja y comprobantes
    '/ventas/nueva':                   'ventas',
    '/clientes/registrar_pos':         'ventas',
    '/caja':                           'ventas',
    '/caja/abrir':                     'ventas',
    '/caja/egreso':                    'ventas',
    '/caja/cerrar':                    'ventas',
    '/ventas/<int:id_venta>/comprobante': 'ventas',
    # pedidos
    '/pedidos':                        'pedidos',
    '/pedidos/detalle/<int:id>':       'pedidos',
    '/pedidos/<int:id>/estado':        'pedidos',
    # reportes: pantalla y todas las exportaciones
    '/reportes':                       'reportes',
    '/reportes/exportar':              'reportes',
    '/reportes/dia/exportar':          'reportes',
    '/reportes/pedidos/exportar':      'reportes',
    '/reportes/productos/exportar':    'reportes',
    '/reportes/clientes/exportar':     'reportes',
    '/reportes/proveedores/exportar':  'reportes',
    '/reportes/inventario/exportar':   'reportes',
    # alertas
    '/alertas':                        'alertas',
    '/alertas/resolver/<int:id>':      'alertas',
    # ofertas
    '/admin/ofertas':                  'ofertas',
    '/admin/ofertas/nueva':            'ofertas',
    '/admin/ofertas/editar/<int:id>':  'ofertas',
    '/admin/ofertas/activar/<int:id>': 'ofertas',
    '/admin/ofertas/desactivar/<int:id>': 'ofertas',
}
