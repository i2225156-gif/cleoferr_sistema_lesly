"""Módulo admin unificado (C2a..C2e): sidebar único; hijas migradas:
dashboard/pedidos/pedido_detalle/ofertas (C2a),
productos/producto_form/producto_detalle/inventario/alertas (C2b),
admin_pagos_simulados/admin_reservas (C2c, rutas solo admin),
clientes/cliente_form/proveedores/proveedor_form/reportes (C2d) y
caja/venta_form (C2e: POS con sidebar_colapsado, caja con sidebar fijo).

Contra la BD real con conexión proxied (rollback): nada persiste. Por
plantilla migrada verifica: 200 para admin y vendedor (donde el rol lo
permite), sidebar presente, ítem activo correcto, ítems solo-admin
ocultos al vendedor (el permiso real se valida en el backend), navbar
viejo ausente en admin y presente en páginas no-admin, y lo restaurado
del navbar (chip usuario, clave, tema, salir).
"""
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


class ConnProxy:
    def __init__(self, raw):
        self._raw = raw

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            return self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self._raw.cursor()

    def commit(self):
        pass

    def rollback(self):
        self._raw.rollback()

    def close(self):
        pass


def _sesion(rol, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    # Venta sintética (solo para leer su detalle; rollback al final)
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), 11,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'), %s)
        RETURNING id_venta
    """, (f"test-adm-{_uuid.uuid4().hex[:12]}",))
    id_venta = cur.fetchone()['id_venta']
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 5.00, 5)"
        " RETURNING id_producto", (f"test-adm-{_uuid.uuid4().hex[:8]}",))
    id_producto = cur.fetchone()['id_producto']
    cur.execute(
        "INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)"
        " VALUES (%s, %s, 1, 5.00)", (id_venta, id_producto))
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'id_venta': id_venta,
           'admin': _sesion('administrador', 7), 'vendedor': _sesion('vendedor', 8)}
    proxy.rollback()
    raw.close()


# ── Dashboard (solo admin) ───────────────────────────────────────

def test_dashboard_admin_sidebar_activo_sin_navbar_viejo(fx):
    r = fx['admin'].get('/admin/dashboard')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # El <nav> del menú superior está anulado (el CSS de su clase sigue en base)
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'CLEOFERR Admin' in html                 # sidebar presente
    assert 'href="/admin/dashboard" class="sidebar-item activo"' in html
    assert 'datosVentas' in html                    # scripts de la hija siguen vivos
    # Restaurado del navbar: clave, tema y chip de usuario
    assert 'Cambiar clave' in html and 'toggleTema()' in html
    assert 'Prueba' in html and 'administrador' in html


def test_dashboard_vendedor_bloqueado(fx):
    assert fx['vendedor'].get('/admin/dashboard').status_code == 302


# ── Pedidos (admin y vendedor) ────────────────────────────────────

def test_pedidos_admin_items_completos(fx):
    r = fx['admin'].get('/pedidos')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/pedidos" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    # Ítems solo-admin visibles para administrador
    assert '/admin/pagos_simulados' in html and '/admin/reservas' in html


def test_pedidos_vendedor_sin_items_solo_admin(fx):
    r = fx['vendedor'].get('/pedidos')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'CLEOFERR Admin' in html                 # sidebar presente
    assert 'href="/pedidos" class="sidebar-item activo"' in html
    # Ocultos para vendedor (permiso real en backend igualmente)
    assert '/admin/dashboard' not in html
    assert '/admin/pagos_simulados' not in html
    assert '/admin/reservas' not in html
    # Lo visible para vendedor sigue intacto
    assert 'href="/admin/ofertas"' in html


def test_pedido_detalle_con_sidebar(fx):
    r = fx['vendedor'].get(f"/pedidos/detalle/{fx['id_venta']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'CLEOFERR Admin' in html
    assert 'href="/pedidos" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html


# ── Ofertas ───────────────────────────────────────────────────────

def test_ofertas_con_sidebar_y_activo(fx):
    r = fx['vendedor'].get('/admin/ofertas')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/admin/ofertas" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html


# ── Navbar viejo intacto fuera de admin ──────────────────────────

def test_navbar_sigue_en_paginas_no_admin(fx):
    html = fx['admin'].get('/catalogo').get_data(as_text=True)
    assert 'navbar navbar-cleoferr navbar-expand-lg' in html  # catálogo lo conserva


# ── C2b: Productos (listado, formulario y detalle) ────────────────

def test_productos_admin_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/productos')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/productos" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'GESTIÓN DE PRODUCTOS' in html           # contenido de la hija vivo


def test_productos_vendedor_sin_items_solo_admin(fx):
    r = fx['vendedor'].get('/productos')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'CLEOFERR Admin' in html                 # sidebar presente
    assert 'href="/productos" class="sidebar-item activo"' in html
    # Ocultos para vendedor (permiso real en backend igualmente)
    assert '/admin/dashboard' not in html
    assert '/admin/pagos_simulados' not in html
    assert '/admin/reservas' not in html


def test_producto_form_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/productos/nuevo')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/productos" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Nuevo Producto' in html


def test_producto_detalle_con_sidebar_y_activo(fx):
    # db.get_or_404 usa la sesión SQLAlchemy (conexión aparte): el producto
    # sintético del fixture vive en una txn sin confirmar invisible para esa
    # sesión, así que se usa un id REAL leído por SELECT (solo lectura).
    fx['cur'].execute(
        "SELECT id_producto FROM producto ORDER BY id_producto LIMIT 1")
    fila = fx['cur'].fetchone()
    assert fila, 'no hay productos en la BD'
    r = fx['admin'].get(f"/productos/detalle/{fila['id_producto']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/productos" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html


# ── C2b: Inventario ───────────────────────────────────────────────

def test_inventario_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/inventario')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/inventario" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Historial de Movimientos' in html


def test_inventario_vendedor_sin_items_solo_admin(fx):
    r = fx['vendedor'].get('/inventario')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/inventario" class="sidebar-item activo"' in html
    assert '/admin/dashboard' not in html
    assert '/admin/pagos_simulados' not in html
    assert '/admin/reservas' not in html


# ── C2b: Alertas ──────────────────────────────────────────────────

def test_alertas_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/alertas')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/alertas" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Listado de alertas' in html
    # El script AJAX ahora viaja inline en admin_contenido: antes era un
    # {% block scripts %} muerto (base.html no lo define) y no cargaba.
    assert 'resolverAlertaAjax' in html


# ── C2c: Pagos simulados y reservas (rutas solo admin) ─────────────

def test_pagos_simulados_admin_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/admin/pagos_simulados')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/admin/pagos_simulados" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Disparar evento simulado' in html       # contenido de la hija vivo


def test_pagos_simulados_vendedor_bloqueado(fx):
    # Ruta solo admin (admin_required): el vendedor recibe redirect, no 200
    assert fx['vendedor'].get('/admin/pagos_simulados').status_code == 302


def test_reservas_admin_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/admin/reservas')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/admin/reservas" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Reservas de stock vencidas' in html      # contenido de la hija vivo


def test_reservas_vendedor_bloqueado(fx):
    # Ruta solo admin (admin_required): el vendedor recibe redirect, no 200
    assert fx['vendedor'].get('/admin/reservas').status_code == 302


# ── C2d: Clientes (listado y formulario; admin y vendedor) ────────

def test_clientes_admin_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/clientes')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/clientes" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'REGISTRO DE USUARIO' in html          # contenido de la hija vivo


def test_clientes_vendedor_sin_items_solo_admin(fx):
    r = fx['vendedor'].get('/clientes')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/clientes" class="sidebar-item activo"' in html
    # Ocultos para vendedor (permiso real en backend igualmente)
    assert '/admin/dashboard' not in html
    assert '/admin/pagos_simulados' not in html
    assert '/admin/reservas' not in html


def test_cliente_form_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/clientes/nuevo')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/clientes" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Nuevo Cliente' in html
    assert 'consultarDoc' in html                  # JS de validaciones vivo


# ── C2d: Proveedores (listado y formulario; admin y vendedor) ─────

def test_proveedores_admin_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/proveedores')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/proveedores" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'PROVEEDORES' in html                  # contenido de la hija vivo


def test_proveedores_vendedor_sin_items_solo_admin(fx):
    r = fx['vendedor'].get('/proveedores')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/proveedores" class="sidebar-item activo"' in html
    assert '/admin/dashboard' not in html
    assert '/admin/pagos_simulados' not in html
    assert '/admin/reservas' not in html


def test_proveedor_form_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/proveedores/nuevo')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/proveedores" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Nuevo Proveedor' in html
    assert 'campoTelProv' in html                  # JS de validacion vivo


# ── C2d: Reportes (admin y vendedor) ──────────────────────────────

def test_reportes_admin_con_sidebar_y_activo(fx):
    r = fx['admin'].get('/reportes')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/reportes" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Generar Reporte' in html              # contenido de la hija vivo


def test_reportes_vendedor_sin_items_solo_admin(fx):
    r = fx['vendedor'].get('/reportes')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/reportes" class="sidebar-item activo"' in html
    assert '/admin/dashboard' not in html
    assert '/admin/pagos_simulados' not in html
    assert '/admin/reservas' not in html


# ── C2e: POS (venta_form) con sidebar colapsado; caja y resto fijos ─

def test_venta_form_admin_sidebar_colapsado(fx):
    r = fx['admin'].get('/ventas/nueva')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # El POS pide pantalla completa: sidebar presente pero off-canvas.
    # Se aserta el marcado exacto: el CSS del sidebar menciona estas clases
    # en TODAS las páginas, lo que cuenta es que estén APLICADAS aquí.
    assert 'CLEOFERR Admin' in html
    assert 'class="admin-sidebar d-flex flex-column admin-sidebar-off"' in html
    assert 'class="admin-hamburguesa admin-hamburguesa-off"' in html
    assert 'class="admin-contenido p-4 admin-contenido-off"' in html
    assert 'href="/caja" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    # JS del POS intacto: catálogo, Enter del escáner, cobro y datos
    assert 'renderCatalogo' in html
    assert "e.key === 'Enter'" in html
    assert 'validarVenta' in html
    assert 'const PRODUCTOS' in html


def test_venta_form_vendedor_sidebar_colapsado(fx):
    r = fx['vendedor'].get('/ventas/nueva')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="admin-sidebar d-flex flex-column admin-sidebar-off"' in html
    # Ocultos para vendedor (permiso real en backend igualmente)
    assert '/admin/dashboard' not in html
    assert '/admin/pagos_simulados' not in html
    assert '/admin/reservas' not in html


def test_caja_admin_sidebar_fijo_normal(fx):
    r = fx['admin'].get('/caja')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/caja" class="sidebar-item activo"' in html
    assert 'navbar navbar-cleoferr navbar-expand-lg' not in html
    assert 'Historial de cierres' in html          # contenido de la hija vivo
    # Sin la variable: nav, hamburguesa y contenedor SIN clases de colapso
    assert 'class="admin-sidebar d-flex flex-column" id="adminSidebar"' in html
    assert '<button class="admin-hamburguesa" onclick=' in html
    assert 'class="admin-contenido p-4">' in html
    assert 'class="admin-sidebar d-flex flex-column admin-sidebar-off"' not in html
    assert 'class="admin-hamburguesa admin-hamburguesa-off"' not in html
    assert 'class="admin-contenido p-4 admin-contenido-off"' not in html


def test_pagina_sin_variable_sidebar_fijo(fx):
    # Cualquier otra página admin (sin sidebar_colapsado) queda igual
    html = fx['admin'].get('/productos').get_data(as_text=True)
    assert 'class="admin-sidebar d-flex flex-column" id="adminSidebar"' in html
    assert 'class="admin-sidebar d-flex flex-column admin-sidebar-off"' not in html
    assert 'class="admin-hamburguesa admin-hamburguesa-off"' not in html
    assert 'class="admin-contenido p-4 admin-contenido-off">' not in html


def test_venta_form_guard_venta_en_curso(fx):
    # Marcador del guard: aviso nativo beforeunload al salir de la página
    # con ticket armado (y no durante el cobro). El diálogo es el nativo.
    html = fx['admin'].get('/ventas/nueva').get_data(as_text=True)
    assert "window.addEventListener('beforeunload'" in html
    assert 'Object.keys(carrito).length > 0 && !cobrando' in html


# ── Fix del flash: zona-flashes alineado con el contenido admin ─────

def test_zona_flashes_presente_en_admin(fx):
    # El wrapper viaja en base y el CSS del sidebar le hace hueco al
    # sidebar fijo (antes quedaban tapados los primeros 240px)
    html = fx['admin'].get('/productos').get_data(as_text=True)
    assert 'class="zona-flashes"' in html
    assert '.zona-flashes { margin-left: 240px; }' in html


def test_zona_flashes_off_en_pos(fx):
    # En el POS (pantalla completa) el wrapper lleva la clase off y el
    # CSS la vuelve a 0
    html = fx['admin'].get('/ventas/nueva').get_data(as_text=True)
    assert 'class="zona-flashes zona-flashes-off"' in html
    assert '.zona-flashes.zona-flashes-off { margin-left: 0; }' in html


def test_zona_flashes_cliente_sin_css_admin(fx):
    # Cliente: el wrapper existe (vive en base) pero SIN el CSS del
    # sidebar admin → sin efecto visual, igual que siempre
    html = fx['admin'].get('/catalogo').get_data(as_text=True)
    assert 'class="zona-flashes"' in html
    assert 'zona-flashes { margin-left' not in html
    assert 'class="zona-flashes zona-flashes-off"' not in html
