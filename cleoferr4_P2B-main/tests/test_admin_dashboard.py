"""Fase D: dashboard del administrador (KPIs + graficos, solo lectura).

Verifica: admin OK, no-admin bloqueado, GET sin escritura, y que cada
KPI coincide con una consulta independiente (contra la BD real).
"""
import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


def _sesion(rol='administrador', uid=7):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
    return c


@pytest.fixture(scope='module')
def admin():
    return _sesion()


def test_dashboard_admin_200(admin):
    r = admin.get('/admin/dashboard')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Panel de Administración' in html
    assert 'Ventas pagadas hoy' in html
    assert 'Ticket promedio' in html


def test_dashboard_vendedor_bloqueado():
    r = _sesion('vendedor', 8).get('/admin/dashboard')
    assert r.status_code == 302


def test_dashboard_cliente_bloqueado():
    r = _sesion('cliente', 11).get('/admin/dashboard')
    assert r.status_code == 302


def test_dashboard_anonimo_bloqueado():
    r = app.test_client().get('/admin/dashboard')
    assert r.status_code == 302


def test_dashboard_get_sin_escritura(admin):
    """El GET no escribe: comprobamos que venta/pago/comprobante no cambian."""
    with db_supabase.engine_tienda.connect() as c:
        antes = {}
        for tabla in ('venta', 'pago', 'comprobante', 'auditoria'):
            antes[tabla] = c.execute(
                __import__('sqlalchemy').text(f"SELECT COUNT(*) FROM {tabla}")).scalar()
    admin.get('/admin/dashboard')
    with db_supabase.engine_tienda.connect() as c:
        for tabla, n in antes.items():
            despues = c.execute(
                __import__('sqlalchemy').text(f"SELECT COUNT(*) FROM {tabla}")).scalar()
            assert despues == n, f"{tabla} cambió de {n} a {despues}"


# ── KPIs contra consultas independientes ─────────────────────────

def test_kpi_ventas_hoy(admin):
    with db_supabase.engine_tienda.connect() as c:
        esperado = float(c.execute(__import__('sqlalchemy').text("""
            SELECT COALESCE(SUM(d.cantidad * d.precio_unitario), 0) AS v
              FROM venta v
              JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
              JOIN detalle_venta d ON d.id_venta = v.id_venta
             WHERE DATE(v.fecha AT TIME ZONE 'UTC' AT TIME ZONE 'America/Lima') = CURRENT_DATE
        """)).scalar() or 0)
    r = admin.get('/admin/dashboard')
    html = r.get_data(as_text=True)
    assert f'S/ {esperado:.2f}' in html


def test_kpi_pendientes_pago(admin):
    with db_supabase.engine_tienda.connect() as c:
        esperado = int(c.execute(__import__('sqlalchemy').text("""
            SELECT COUNT(*) FROM venta v
              JOIN pago p ON p.id_venta = v.id_venta AND p.estado = 'pendiente'
              JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
              JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
             WHERE ev.nombre = 'pendiente' AND tv.nombre <> 'local'
        """)).scalar())
    r = admin.get('/admin/dashboard')
    html = r.get_data(as_text=True)
    # buscar el número en el HTML de la tarjeta
    assert str(esperado) in html


def test_kpi_stock_bajo(admin):
    with db_supabase.engine_tienda.connect() as c:
        esperado = int(c.execute(__import__('sqlalchemy').text("""
            SELECT COUNT(*) FROM producto
             WHERE activo = TRUE AND stock < COALESCE(stock_minimo, 15)
        """)).scalar())
    r = admin.get('/admin/dashboard')
    html = r.get_data(as_text=True)
    assert str(esperado) in html


def test_grafico_ventas_7_dias(admin):
    """El grafico de ventas tiene 7 barras (generate_series llena dias vacios)."""
    r = admin.get('/admin/dashboard')
    html = r.get_data(as_text=True)
    # Chart.js inyecta los datos como JSON; verificar que hay 7 etiquetas
    # buscando el patron de datosVentas en el JS
    assert 'datosVentas' in html
    # contar DD/MM en el JSON
    import re
    etiquetas = re.findall(r'"etiqueta":\s*"(\d{2}/\d{2})"', html)
    assert len(etiquetas) == 7, f"esperaba 7 días, encontré {len(etiquetas)}"


def test_grafico_estados_6(admin):
    """El grafico de pedidos tiene los 6 estados actuales."""
    r = admin.get('/admin/dashboard')
    html = r.get_data(as_text=True)
    for estado in ('pendiente', 'confirmado', 'preparando', 'listo', 'entregado', 'cancelado'):
        assert estado in html
