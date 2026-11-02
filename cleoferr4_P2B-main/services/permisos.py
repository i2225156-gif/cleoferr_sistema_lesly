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
    '/productos/categorias/nueva':     'productos',
    '/productos/marcas/nueva':         'productos',
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
    '/reportes/generar':               'reportes',
    '/reportes/rango/exportar':        'reportes',
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


# ── Mapa de protección: rutas SOLO-ADMIN ──────────────────────────
# Rutas que NUNCA llevan permiso de módulo: las protege admin_required
# (el administrador tiene todo; un vendedor no entra nunca). El mapa
# documenta qué endpoint protege cada regla y el test de cobertura lo
# contrasta con app.url_map.
RUTAS_SOLO_ADMIN = {
    '/admin/dashboard':                   'admin_dashboard',
    '/admin/pagos_simulados':             'admin_pagos_simulados',
    '/admin/pagos_simulados/disparar':    'admin_pagos_disparar',
    '/admin/pagos_simulados/pago/<int:id_pago>/<accion>':
        'admin_pagos_verificacion',
    '/admin/pedidos/expirar_pendientes':  'admin_expirar_pendientes',
    '/admin/reservas':                    'admin_reservas',
    '/admin/reservas/liberar':            'admin_reservas_liberar',
    '/admin/usuarios':                    'admin_usuarios',
    '/admin/usuarios/nuevo':              'admin_usuarios_nuevo',
    '/admin/usuarios/<int:id>/permisos':  'admin_usuarios_permisos',
    '/admin/usuarios/<int:id>/estado':    'admin_usuarios_estado',
    '/admin/usuarios/<int:id>/desbloquear': 'admin_usuarios_desbloquear',
    '/clientes/eliminar/<int:id>':        'eliminar_cliente',
    '/productos/eliminar/<int:id>':       'eliminar_producto',
    '/proveedores/eliminar/<int:id>':     'eliminar_proveedor',
}

# Módulos que NUNCA se asignan a un vendedor: son solo del administrador
# (dashboard, pagos simulados, reservas, los borrados y el propio panel
# de usuarios). Por eso NO aparecen como casillas en el panel: no están
# en CATALOGO.
MODULOS_SOLO_ADMIN = ('dashboard', 'pagos_simulados', 'reservas',
                       'borrados', 'usuarios')


# ── Panel de usuarios (SOLO ADMIN) ────────────────────────────────
def personal(conn):
    """Personal (administradores y vendedores) con sus módulos asignados.

    Cada fila trae id_usuario, email, rol, activo, intentos_fallidos,
    bloqueado_hasta, el estado calculado (activo/bloqueado/inactivo),
    es_admin y el conjunto de módulos de usuario_permiso (permisos
    activos). NO comitea: el llamador decide.
    """
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id_usuario, u.email, r.nombre AS rol, u.activo,
               u.intentos_fallidos, u.bloqueado_hasta
          FROM usuario u
          JOIN rol r ON r.id_rol = u.id_rol
         WHERE LOWER(TRIM(r.nombre)) IN ('administrador', 'vendedor')
         ORDER BY CASE WHEN LOWER(TRIM(r.nombre)) = 'administrador'
                        THEN 0 ELSE 1 END,
                  u.id_usuario
    """)
    filas = cursor.fetchall()
    cursor.execute("""
        SELECT up.id_usuario, p.nombre
          FROM usuario_permiso up
          JOIN permiso p ON p.id_permiso = up.id_permiso
         WHERE p.activo = TRUE
    """)
    asignados = {}
    for fila in cursor.fetchall():
        asignados.setdefault(fila['id_usuario'], set()).add(fila['nombre'])
    for fila in filas:
        fila['es_admin'] = (fila['rol'] or '').strip().lower() == 'administrador'
        fila['permisos'] = asignados.get(fila['id_usuario'], set())
        fila['estado'] = estado_cuenta(fila)
    return filas


def rol_de_usuario(conn, id_usuario):
    """Rol (en minúsculas) y estado de una cuenta; None si no existe."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT LOWER(TRIM(r.nombre)) AS rol, u.activo
          FROM usuario u
          JOIN rol r ON r.id_rol = u.id_rol
         WHERE u.id_usuario = %s
    """, (id_usuario,))
    return cursor.fetchone()


def estado_cuenta(fila, ahora=None):
    """Estado legible de la cuenta: activo | bloqueado | inactivo."""
    from datetime import datetime, timezone
    if not fila.get('activo'):
        return 'inactivo'
    hasta = fila.get('bloqueado_hasta')
    if hasta and hasta > (ahora or datetime.now(timezone.utc)):
        return 'bloqueado'
    return 'activo'


def administradores_activos(conn, excluir_id=None):
    """Cuántos administradores ACTIVOS quedan (opcionalmente sin uno)."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT COUNT(*) AS n
          FROM usuario u
          JOIN rol r ON r.id_rol = u.id_rol
         WHERE u.activo = TRUE
           AND LOWER(TRIM(r.nombre)) = 'administrador'
           AND (%s IS NULL OR u.id_usuario <> %s)
    """, (excluir_id, excluir_id))
    return int(cursor.fetchone()['n'])


def sincronizar_permisos(conn, id_admin, id_usuario, claves):
    """Deja los módulos del vendedor EXACTAMENTE como la lista recibida.

    Lista blanca estricta: una clave fuera de CATALOGO se rechaza con
    ValueError y NO se modifica nada. Da y quita con los helpers
    auditados (asignado_por = id_admin). NO comitea: el llamador
    decide (una sola transacción).
    """
    desconocidas = sorted({c for c in claves if c not in CATALOGO})
    if desconocidas:
        raise ValueError('Módulo no permitido: %s' % ', '.join(desconocidas))
    dados = set(claves)
    for clave in CATALOGO:
        if clave in dados:
            dar_permiso(conn, id_admin, id_usuario, clave)
        else:
            quitar_permiso(conn, id_usuario, clave)
    return sorted(dados)


def establecer_estado(conn, id_usuario, activo):
    """Activa o desactiva la cuenta (baja lógica). NO comitea."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        UPDATE usuario SET activo = %s
         WHERE id_usuario = %s
        RETURNING id_usuario
    """, (bool(activo), id_usuario))
    return cursor.fetchone() is not None


def desbloquear_cuenta(conn, id_usuario):
    """Intentos fallidos a 0 y bloqueo retirado. NO comitea."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        UPDATE usuario
           SET intentos_fallidos = 0,
               bloqueado_hasta = NULL
         WHERE id_usuario = %s
        RETURNING id_usuario
    """, (id_usuario,))
    return cursor.fetchone() is not None
