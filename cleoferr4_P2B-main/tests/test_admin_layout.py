"""Módulo admin unificado (C2a): sidebar único + dashboard/pedidos/ofertas.

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
