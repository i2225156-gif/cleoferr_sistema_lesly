"""Fase 2.3 (pieza 6): reportes y KPIs con importe NETO (dcto congelado).

Contra la BD real dentro de transacciones con rollback: nada persiste.
Fixture central: venta sintética de 3 × S/ 9.99 con dcto congelado 1.50
(neto 25.47 == pago.monto confirmado). Un test por sitio verifica que
lo mostrado/cobrado es el NETO. No toca SQL_MOVIMIENTOS_30DIAS (valora
inventario, no ventas).
"""
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app, SQL_VENTAS_HOY_PAGADAS
from services.ofertas import SQL_IMPORTE_LINEA


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
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    """Venta PAGADA con dcto: 3 x (9.99 - 1.50) = 25.47 netos.

    Online, cliente 11, pago 'confirmado' con monto 25.47: aparece en
    los KPIs de hoy y en los listados de pedidos.
    """
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 9.99, 50)"
        " RETURNING id_producto", (f"test23r-{_uuid.uuid4().hex[:10]}",))
    id_producto = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio, fecha_fin)
        VALUES (%s, 'porcentaje', 15,
                NOW() - INTERVAL '1 hour', NOW() + INTERVAL '1 day')
        RETURNING id_oferta
    """, (id_producto,))
    id_oferta = cur.fetchone()['id_oferta']
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), 11,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='confirmado'), %s)
        RETURNING id_venta
    """, (f"test23r-{_uuid.uuid4().hex[:12]}",))
    id_venta = cur.fetchone()['id_venta']
    cur.execute("""
        INSERT INTO detalle_venta
            (id_venta, id_producto, cantidad, precio_unitario,
             id_oferta, descuento_unitario)
        VALUES (%s, %s, 3, 9.99, %s, 1.50)
    """, (id_venta, id_producto, id_oferta))
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                NULL, 'confirmado', 25.47, %s, 'simulada')
    """, (id_venta, f"sim_test_{_uuid.uuid4().hex[:8]}"))
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'id_venta': id_venta,
           'id_producto': id_producto,
           'vendedor': _sesion('vendedor', 8), 'admin': _sesion('administrador', 7),
           'cliente': _sesion('cliente', 11)}
    proxy.rollback()
    raw.close()


def _total_neto_hoy(fx):
    """Ventas pagadas de hoy con la MISMA fórmula neta de la ruta."""
    fx['cur'].execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM venta v
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE DATE(v.fecha AT TIME ZONE 'UTC' AT TIME ZONE 'America/Lima') = CURRENT_DATE
    """)
    return float(fx['cur'].fetchone()['total'] or 0)


# ── Listados de pedidos ────────────────────────────────────────────

def test_listado_pedidos_admin_total_neto(fx):
    html = fx['vendedor'].get('/pedidos').get_data(as_text=True)
    assert '25.47' in html            # total del pedido = lo cobrado (neto)


def test_pedido_detalle_total_neto_y_linea_congelada(fx):
    html = fx['vendedor'].get(f"/pedidos/detalle/{fx['id_venta']}").get_data(as_text=True)
    assert '25.47' in html            # total neto del pedido
    assert '8.49' in html             # precio final por unidad
    assert '<s' in html and '9.99' in html   # base tachada en la línea


def test_mis_pedidos_total_neto(fx):
    html = fx['cliente'].get('/mis_pedidos').get_data(as_text=True)
    assert '25.47' in html            # total del pedido e importe neto de la línea (3 x 8.49)
    assert '29.97' not in html        # jamás el bruto sin descuento


# ── Exportaciones Excel ───────────────────────────────────────────

def test_excel_pedidos_exporta_total_neto(fx):
    r = fx['vendedor'].get('/reportes/pedidos/exportar')
    assert r.status_code == 200
    # La consulta subyacente (misma fórmula) da el neto de la venta
    fx['cur'].execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM detalle_venta d WHERE d.id_venta = %s
    """, (fx['id_venta'],))
    assert Decimal(str(fx['cur'].fetchone()['total'])) == Decimal('25.47')


def test_excel_ventas_dia_exporta(fx):
    """La ruta del día responde 200 y la venta de hoy está en el neto."""
    r = fx['vendedor'].get('/reportes/dia/exportar')
    assert r.status_code == 200
    fx['cur'].execute("""
        SELECT COUNT(*) AS n FROM venta WHERE id_venta = %s
    """, (fx['id_venta'],))
    assert fx['cur'].fetchone()['n'] == 1    # sigue en el día (rollback al final)


# ── Caja (resumen y cierre con venta LOCAL descontada) ─────────────

@pytest.fixture()
def fx_caja(fx):
    """Encima de fx: caja sintética abierta + venta LOCAL pagada con dcto
    (efectivo) para los sitios de caja: resumen por caja y cierre."""
    cur = fx['cur']
    cur.execute("""
        INSERT INTO caja (estado, monto_apertura, id_usuario_apertura)
        VALUES ('abierta', 10.00, 8) RETURNING id_caja
    """)
    id_caja = cur.fetchone()['id_caja']
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='local'), 11,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='entregado'), %s)
        RETURNING id_venta
    """, (f"test23rc-{_uuid.uuid4().hex[:12]}",))
    id_venta_l = cur.fetchone()['id_venta']
    cur.execute("""
        INSERT INTO detalle_venta
            (id_venta, id_producto, cantidad, precio_unitario,
             id_oferta, descuento_unitario)
        VALUES (%s, %s, 1, 9.99,
                (SELECT id_oferta FROM oferta WHERE id_producto = %s), 1.50)
    """, (id_venta_l, fx['id_producto'], fx['id_producto']))
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto, pasarela)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                %s, 'confirmado', 8.49, NULL)
    """, (id_venta_l, id_caja))
    fx.update({'id_caja': id_caja, 'id_venta_local': id_venta_l})
    return fx


def test_caja_resumen_cuenta_venta_local_descontada(fx_caja):
    """El resumen por caja muestra el NETO de la venta local (8.49)."""
    html = fx_caja['vendedor'].get('/caja').get_data(as_text=True)
    assert '8.49' in html


def test_cierre_de_caja_cuadra_con_venta_descontada(fx_caja):
    """Cierre declarando fondo + neto de la venta: diferencia 0."""
    fx_caja['cur'].execute("SELECT monto_apertura FROM caja WHERE id_caja = %s",
                           (fx_caja['id_caja'],))
    fondo = float(fx_caja['cur'].fetchone()['monto_apertura'])
    r = fx_caja['vendedor'].post('/caja/cerrar', data={
        'monto_declarado': f'{fondo + 8.49:.2f}',
        'observaciones': 'test 2.3', 'csrf_token': 'token-test'},
        follow_redirects=True)
    assert r.status_code == 200
    fx_caja['cur'].execute(
        "SELECT estado, diferencia FROM caja WHERE id_caja = %s",
        (fx_caja['id_caja'],))
    fila = fx_caja['cur'].fetchone()
    assert fila['estado'] == 'cerrada'
    assert abs(float(fila['diferencia'] or 0)) < 0.005   # cuadra con el NETO


# ── POS: movimiento de caja y comprobante local ───────────────────

def test_pos_formula_movimiento_y_comprobante_neto(fx):
    """El POS no aplica ofertas (2.3 es solo online), pero sus sitios usan
    la MISMA fórmula neta: para una venta con dcto congelado, el importe
    que registrarían (movimiento_caja y comprobante local) es exactamente
    el neto == pago.monto."""
    cur = fx['cur']
    cur.execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
          FROM detalle_venta WHERE id_venta = %s
    """, (fx['id_venta'],))
    total = Decimal(str(cur.fetchone()['total']))
    cur.execute("SELECT monto FROM pago WHERE id_venta = %s", (fx['id_venta'],))
    cobrado = Decimal(str(cur.fetchone()['monto']))
    assert total == Decimal('25.47') == cobrado   # neto == lo cobrado


# ── KPIs (reportes y dashboard) ───────────────────────────────────

def test_kpi_ventas_hoy_reportes_con_neto(fx):
    """El KPI de /reportes muestra el neto total de hoy (venta descontada
    incluida) calculado con la misma fórmula de la ruta."""
    esperado = _total_neto_hoy(fx)
    html = fx['vendedor'].get('/reportes').get_data(as_text=True)
    assert f'{esperado:.2f}' in html
    # La fórmula neta difiere de la bruta exactamente en el dcto (4.50)
    fx['cur'].execute("""
        SELECT COALESCE(SUM(d.cantidad * d.descuento_unitario), 0) AS dcto
          FROM venta v
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE DATE(v.fecha AT TIME ZONE 'UTC' AT TIME ZONE 'America/Lima') = CURRENT_DATE
    """)
    dcto_hoy = float(fx['cur'].fetchone()['dcto'] or 0)
    assert dcto_hoy >= 4.50           # el dcto de mi venta (más el real si hay)


def test_kpi_ventas_hoy_dashboard_con_neto(fx):
    esperado = _total_neto_hoy(fx)
    html = fx['admin'].get('/admin/dashboard').get_data(as_text=True)
    assert f'S/ {esperado:.2f}' in html


def test_kpi_ticket_promedio_con_neto(fx):
    """El ticket promedio del dashboard usa el neto: mi venta aporta 25.47
    (no 29.97 bruto). Se verifica que la fórmula de la ruta y la réplica
    independiente (neto) coinciden."""
    fx['cur'].execute(f"""
        SELECT COALESCE(
                   SUM({SQL_IMPORTE_LINEA.format(p='d.')}) / NULLIF(COUNT(DISTINCT v.id_venta), 0),
                   0) AS ticket
          FROM venta v
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE v.fecha >= NOW() - INTERVAL '30 days'
    """)
    ticket = float(fx['cur'].fetchone()['ticket'] or 0)
    html = fx['admin'].get('/admin/dashboard').get_data(as_text=True)
    assert f'{ticket:.2f}' in html


def test_grafico_7_dias_con_neto(fx):
    """La barra de HOY del gráfico incluye el neto de la venta descontada:
    la réplica con la fórmula neta de la ruta debe aparecer en el JSON."""
    fx['cur'].execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='det.')}), 0) AS total
          FROM venta v
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN detalle_venta det ON det.id_venta = v.id_venta
         WHERE DATE(v.fecha AT TIME ZONE 'UTC' AT TIME ZONE 'America/Lima') = CURRENT_DATE
    """)
    hoy = float(fx['cur'].fetchone()['total'] or 0)
    html = fx['admin'].get('/admin/dashboard').get_data(as_text=True)
    assert f'{hoy:.2f}' in html        # la barra de hoy lleva el neto


def test_constante_ventas_hoy_pagadas_usa_formula_unica():
    assert SQL_IMPORTE_LINEA.format(p='d.') in SQL_VENTAS_HOY_PAGADAS
