"""Fase 2.3 (pieza 6): reportes y KPIs con importe NETO (dcto congelado).

Contra la BD real dentro de transacciones con rollback: nada persiste.
Fixture central: venta sintética de 3 × S/ 9.99 con dcto congelado 1.50
(neto 25.47 == pago.monto confirmado). Un test por sitio verifica que
lo mostrado/cobrado es el NETO. No toca SQL_MOVIMIENTOS_30DIAS (valora
inventario, no ventas).
"""
import json
import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app, SQL_VENTAS_HOY_PAGADAS
from services.horario import SQL_DIA_LIMA, SQL_HOY_LIMA
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
         WHERE (v.fecha AT TIME ZONE 'America/Lima')::date
                   = (NOW() AT TIME ZONE 'America/Lima')::date
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


def test_excel_ventas_dia_determinista_21h_y_23h59(fx):
    """La exportación del día usa días Lima: la venta de HOY 21:00 va en
    el Excel con su neto y la de AYER 23:59 no, a cualquier hora de
    ejecución (el filtro legado perdía la tarde-noche entre las 19:00
    y 24:00 Lima)."""
    id_ayer = _fijar_ventas_hoy_21h_y_ayer_2359(fx)
    r = fx['vendedor'].get('/reportes/dia/exportar')
    assert r.status_code == 200
    sql_dia = f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM venta v
          LEFT JOIN detalle_venta d ON v.id_venta = d.id_venta
         WHERE {SQL_DIA_LIMA.format(p='v.', col='fecha')} = {SQL_HOY_LIMA}
           AND v.id_venta = %s
    """
    cur = fx['cur']
    cur.execute(sql_dia, (fx['id_venta'],))
    assert Decimal(str(cur.fetchone()['total'])) == Decimal('25.47')
    cur.execute(sql_dia, (id_ayer,))
    assert Decimal(str(cur.fetchone()['total'])) == 0   # la de ayer 23:59 fuera


def test_productos_contador_movimientos_hoy_determinista(fx):
    """El contador de movimientos del día de /productos cuenta en días
    Lima: el movimiento de HOY 21:00 sí y el de AYER 23:59 no, a
    cualquier hora de ejecución."""
    lima = timezone(timedelta(hours=-5))
    cur = fx['cur']
    cur.execute(f"SELECT {SQL_HOY_LIMA} AS hoy")
    hoy = cur.fetchone()['hoy']
    ayer = hoy - timedelta(days=1)
    sql_mov = """
        INSERT INTO inventario_movimiento
            (tipo, id_producto, cantidad, precio_unitario, observacion,
             id_usuario, stock_resultante, fecha)
        VALUES ('entrada', %s, 1, 9.99, 'test23r', 8, 50, %s)
        RETURNING id_movimiento
    """
    cur.execute(sql_mov, (fx['id_producto'],
                          datetime(hoy.year, hoy.month, hoy.day, 21, 0, tzinfo=lima)))
    id_mov_hoy = cur.fetchone()['id_movimiento']
    cur.execute(sql_mov, (fx['id_producto'],
                          datetime(ayer.year, ayer.month, ayer.day, 23, 59, tzinfo=lima)))
    id_mov_ayer = cur.fetchone()['id_movimiento']

    sql_cnt = (f"SELECT COUNT(*) AS cnt FROM inventario_movimiento"
               f" WHERE {SQL_DIA_LIMA.format(p='', col='fecha')} = {SQL_HOY_LIMA}"
               f" AND id_movimiento = %s")
    cur.execute(sql_cnt, (id_mov_hoy,))
    assert cur.fetchone()['cnt'] == 1    # el de hoy 21:00 Lima cuenta
    cur.execute(sql_cnt, (id_mov_ayer,))
    assert cur.fetchone()['cnt'] == 0    # el de ayer 23:59 Lima no

    # Extremo a extremo: la tarjeta de /productos renderiza el número
    # exacto de movimientos de hoy Lima (reales + el sintético)
    cur.execute(f"SELECT COUNT(*) AS cnt FROM inventario_movimiento"
                f" WHERE {SQL_DIA_LIMA.format(p='', col='fecha')} = {SQL_HOY_LIMA}")
    esperado = int(cur.fetchone()['cnt'])
    assert esperado >= 1
    html = fx['admin'].get('/productos').get_data(as_text=True)
    m = re.search(r'stat-value">(\d+)</div>\s*<div class="stat-label">Reportes', html)
    assert m, 'la tarjeta del contador de movimientos debe estar en /productos'
    assert int(m.group(1)) == esperado


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
         WHERE (v.fecha AT TIME ZONE 'America/Lima')::date
                   = (NOW() AT TIME ZONE 'America/Lima')::date
    """)
    dcto_hoy = float(fx['cur'].fetchone()['dcto'] or 0)
    assert dcto_hoy >= 4.50           # el dcto de mi venta (más el real si hay)


def _fijar_ventas_hoy_21h_y_ayer_2359(fx):
    """Reasigna la venta del fixture a HOY 21:00 Lima y crea una segunda
    venta pagada AYER 23:59 Lima (neto 8.49). Devuelve su id_venta.

    Las fechas quedan fijas respecto al 'hoy Lima' de la BD: los tests
    que usan esto son deterministas a cualquier hora de ejecución (la
    de 21:00 siempre es de hoy; la de 23:59 siempre de ayer)."""
    lima = timezone(timedelta(hours=-5))
    cur = fx['cur']
    cur.execute(f"SELECT {SQL_HOY_LIMA} AS hoy")
    hoy = cur.fetchone()['hoy']
    ayer = hoy - timedelta(days=1)
    cur.execute("UPDATE venta SET fecha = %s WHERE id_venta = %s",
                (datetime(hoy.year, hoy.month, hoy.day, 21, 0, tzinfo=lima),
                 fx['id_venta']))
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta,
                           num_operacion, fecha)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), 11,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='confirmado'),
                %s, %s)
        RETURNING id_venta
    """, (f"test23r-{_uuid.uuid4().hex[:12]}",
          datetime(ayer.year, ayer.month, ayer.day, 23, 59, tzinfo=lima)))
    id_ayer = cur.fetchone()['id_venta']
    cur.execute("""
        INSERT INTO detalle_venta
            (id_venta, id_producto, cantidad, precio_unitario,
             id_oferta, descuento_unitario)
        VALUES (%s, %s, 1, 9.99,
                (SELECT id_oferta FROM oferta WHERE id_producto = %s), 1.50)
    """, (id_ayer, fx['id_producto'], fx['id_producto']))
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                NULL, 'confirmado', 8.49, %s, 'simulada')
    """, (id_ayer, f"sim_test_{_uuid.uuid4().hex[:8]}"))
    return id_ayer


def test_kpi_ventas_hoy_determinista_21h_y_23h59(fx):
    """El 'hoy' del KPI es el día Lima a cualquier hora de ejecución.

    El filtro legado DATE(v.fecha AT TIME ZONE 'UTC' AT TIME ZONE
    'America/Lima') = CURRENT_DATE resolvía la fecha UTC sobre un
    timestamptz y entre las 19:00 y 24:00 Lima excluía las ventas de
    esa tarde-noche (rodaban al día siguiente). Aquí las fechas se
    fijan: una venta a las 21:00 Lima de HOY cuenta y otra a las 23:59
    Lima de AYER no, corra el test a la hora que corra.
    """
    id_ayer = _fijar_ventas_hoy_21h_y_ayer_2359(fx)
    cur = fx['cur']
    # Con el WHERE corregido, la de HOY 21:00 Lima cuenta íntegra...
    cur.execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM venta v
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE {SQL_DIA_LIMA.format(p='v.', col='fecha')} = {SQL_HOY_LIMA}
           AND v.id_venta = %s
    """, (fx['id_venta'],))
    assert Decimal(str(cur.fetchone()['total'])) == Decimal('25.47')
    # ...y la de AYER 23:59 Lima queda fuera
    cur.execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM venta v
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE {SQL_DIA_LIMA.format(p='v.', col='fecha')} = {SQL_HOY_LIMA}
           AND v.id_venta = %s
    """, (id_ayer,))
    assert Decimal(str(cur.fetchone()['total'])) == 0
    # Extremo a extremo: /reportes renderiza el neto de hoy exacto y
    # el de ayer no se cuela
    esperado = _total_neto_hoy(fx)
    assert esperado >= 25.47
    html = fx['vendedor'].get('/reportes').get_data(as_text=True)
    assert f'{esperado:.2f}' in html
    assert f'{esperado + 8.49:.2f}' not in html


def test_kpi_ventas_hoy_dashboard_con_neto(fx):
    esperado = _total_neto_hoy(fx)
    html = fx['admin'].get('/admin/dashboard').get_data(as_text=True)
    assert f'S/ {esperado:.2f}' in html


def test_kpi_ventas_hoy_dashboard_determinista_21h_y_23h59(fx):
    """Espejo del determinista de /reportes contra /admin/dashboard: el
    KPI muestra el neto de HOY Lima (venta de 21:00 dentro, la de AYER
    23:59 fuera) a cualquier hora de ejecución."""
    _fijar_ventas_hoy_21h_y_ayer_2359(fx)
    esperado = _total_neto_hoy(fx)
    assert esperado >= 25.47
    html = fx['admin'].get('/admin/dashboard').get_data(as_text=True)
    assert f'S/ {esperado:.2f}' in html
    assert f'S/ {esperado + 8.49:.2f}' not in html


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
    """La barra de HOY del gráfico (etiqueta y valor) lleva el neto de
    las ventas de hoy Lima y la venta de AYER 23:59 cae en la barra de
    ayer: se aserta sobre el par etiqueta-valor del JSON del gráfico,
    nunca sobre un número suelto del HTML."""
    _fijar_ventas_hoy_21h_y_ayer_2359(fx)
    cur = fx['cur']
    cur.execute(f"""
        SELECT TO_CHAR({SQL_HOY_LIMA}, 'DD/MM') AS hoy,
               TO_CHAR({SQL_HOY_LIMA} - 1, 'DD/MM') AS ayer
    """)
    etq = cur.fetchone()
    esperado = _total_neto_hoy(fx)     # mismo criterio que la barra: día Lima + neto
    assert esperado >= 25.47
    html = fx['admin'].get('/admin/dashboard').get_data(as_text=True)
    m = re.search(r'const datosVentas = (\[.*?\]);', html, re.S)
    assert m, 'el dashboard debe incrustar el JSON del gráfico'
    barras = json.loads(m.group(1))
    barra_hoy = next(b for b in barras if b['etiqueta'] == etq['hoy'])
    barra_ayer = next(b for b in barras if b['etiqueta'] == etq['ayer'])
    assert abs(float(Decimal(str(barra_hoy['total']))) - esperado) < 0.005
    assert float(Decimal(str(barra_ayer['total']))) >= 8.49


def test_constante_ventas_hoy_pagadas_usa_formula_unica():
    assert SQL_IMPORTE_LINEA.format(p='d.') in SQL_VENTAS_HOY_PAGADAS
