"""Catálogo de permisos por módulo del personal (TAREA permisos vendedores).

El ADMINISTRADOR tiene SIEMPRE todos los módulos (se impone en la
aplicación, no hay filas para él). El VENDEDOR solo usa lo registrado en
usuario_permiso (BD Auth); si la migración auth_03_permisos.sql aún no
fue aplicada (tabla inexistente), la app cae a POR_DEFECTO_VENDEDOR sin
romper la petición.

'compras' existe en el catálogo para el módulo futuro: hoy no tiene
ninguna ruta asignada.
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

# Semilla de auth_03_permisos.sql: los vendedores existentes arrancan con
# estos módulos (CROSS JOIN ... ON CONFLICT DO NOTHING).
POR_DEFECTO_VENDEDOR = ('ventas', 'pedidos', 'clientes', 'alertas')

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
