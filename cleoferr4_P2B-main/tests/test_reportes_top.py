"""RF12: productos más vendidos (unidades y monto) con rango de fechas.

- Solo cuentan ventas con pago confirmado (criterio §6.9); rango en días
  calendario Lima; sin fechas, todo el historial. Top 10 por unidades y
  por monto con id de desempate.
- Determinista con fechas FIJAS; el conjunto esperado se deriva en
  runtime con la misma consulta (los tests no dependen de las ventas
  reales del rango).
- La venta PENDIENTE y la venta fuera del rango no cuentan.
"""
from datetime import date
import html as _html

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.horario import SQL_DIA_LIMA

MARCA = 'zzqtopf'


class ConnProxy:
    """Contrato de conexión proxied con rollback (como P1..P4)."""

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


def _vendedor():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)

    # Tres productos sintéticos con precios distintos
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock)
        VALUES (%s, 10.00, 50), (%s, 3.00, 50), (%s, 5.00, 50)
        RETURNING id_producto, nombre
    """, (f'{MARCA}-A', f'{MARCA}-B', f'{MARCA}-C'))
    filas = cur.fetchall()
    ids = {r['nombre']: r['id_producto'] for r in filas}

    def _venta(fecha_sql, estado_pago, lineas):
        cur.execute(f"""
            INSERT INTO venta (id_tipo_venta, id_estado_venta, num_operacion, fecha)
            VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                    (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
                    'testrf12-' || md5(random()::text),
                    {fecha_sql})
            RETURNING id_venta
        """)
        id_venta = cur.fetchone()['id_venta']
        for id_prod, cant, precio in lineas:
            cur.execute("""
                INSERT INTO detalle_venta (id_venta, id_producto, cantidad,
                                           precio_unitario, descuento_unitario)
                VALUES (%s, %s, %s, %s, 0)
            """, (id_venta, id_prod, cant, precio))
        total = sum(c * p for _, c, p in lineas)
        cur.execute("""
            INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                              id_transaccion, pasarela)
            VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                    NULL, %s, %s, %s, 'simulada')
        """, (id_venta, estado_pago, total, f'sim_{MARCA}_{id_venta}'))
        return id_venta

    # Dentro del rango 05-12/10: A x2 (S/ 20) y B x5 (S/ 15)
    _venta("'2026-10-10 12:00:00-05'::timestamptz", 'confirmado',
           [(ids[f'{MARCA}-A'], 2, 10.00), (ids[f'{MARCA}-B'], 5, 3.00)])
    # Fuera del rango (01/10): C x4 — solo cuenta sin rango
    _venta("'2026-10-01 12:00:00-05'::timestamptz", 'confirmado',
           [(ids[f'{MARCA}-C'], 4, 5.00)])
    # PENDIENTE dentro del rango: C x1 — NUNCA cuenta
    _venta("'2026-10-10 13:00:00-05'::timestamptz", 'pendiente',
           [(ids[f'{MARCA}-C'], 1, 5.00)])

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'c': _vendedor(), 'cur': cur, 'ids': ids}
    proxy.rollback()
    raw.close()


def _top_esperado(fx, orden, desde=None, hasta=None):
    """Misma consulta de la ruta, calculada por separado."""
    sql_rango = ""
    params = []
    if desde:
        sql_rango += f" AND {SQL_DIA_LIMA.format(p='v.', col='fecha')} >= %s"
        params.append(date.fromisoformat(desde))
    if hasta:
        sql_rango += f" AND {SQL_DIA_LIMA.format(p='v.', col='fecha')} <= %s"
        params.append(date.fromisoformat(hasta))
    cur = fx['cur']
    cur.execute(f"""
        SELECT p.id_producto, p.nombre,
               SUM(d.cantidad) AS unidades,
               SUM(d.cantidad * (d.precio_unitario - COALESCE(d.descuento_unitario, 0))) AS monto
          FROM detalle_venta d
          JOIN venta v ON v.id_venta = d.id_venta
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN producto p ON p.id_producto = d.id_producto
         WHERE 1=1{sql_rango}
         GROUP BY p.id_producto, p.nombre
         ORDER BY {orden} DESC, p.id_producto ASC
         LIMIT 10
    """, params)
    return cur.fetchall()


def _seccion(html, desde_marca, hasta_marca):
    ini = html.find(desde_marca)
    fin = html.find(hasta_marca, ini)
    return html[ini:fin]


def test_top_en_rango_con_orden_correcto(fx):
    r = fx['c'].get('/reportes?desde=2026-10-05&hasta=2026-10-12')
    assert r.status_code == 200
    # los nombres viajan HTML-escapados (p. ej. 'Codo PVC 1/2" 90°'):
    # se des-escapan para comparar planos
    html = _html.unescape(r.get_data(as_text=True))
    esperados_u = _top_esperado(fx, 'unidades', '2026-10-05', '2026-10-12')
    esperados_m = _top_esperado(fx, 'monto', '2026-10-05', '2026-10-12')
    # los esperados (runtime) contienen a los sintéticos; el render también
    for fila in esperados_u + esperados_m:
        assert fila['nombre'] in html, fila['nombre']
    # por unidades: B (5) va antes que A (2)
    sec_u = _seccion(html, 'Por unidades', 'Por monto')
    assert sec_u.index(f'{MARCA}-B') < sec_u.index(f'{MARCA}-A')
    # por monto: A (S/ 20) va antes que B (S/ 15)
    sec_m = html[html.find('Por monto'):]
    assert sec_m.index(f'{MARCA}-A') < sec_m.index(f'{MARCA}-B')


def test_pendiente_y_fuera_de_rango_no_cuentan(fx):
    html = fx['c'].get('/reportes?desde=2026-10-05&hasta=2026-10-12'
                       ).get_data(as_text=True)
    # C solo tiene venta confirmada el 01/10 (fuera) y una pendiente (nunca)
    assert f'{MARCA}-C' not in html


def test_sin_rango_es_todo_el_historial(fx):
    html = fx['c'].get('/reportes').get_data(as_text=True)
    # la venta confirmada del 01/10 de C ahora sí cuenta
    assert f'{MARCA}-C' in html


def test_rango_sin_ventas_muestra_estado_vacio(fx):
    esperados = _top_esperado(fx, 'unidades', '2026-09-01', '2026-09-02')
    assert esperados == []
    html = fx['c'].get('/reportes?desde=2026-09-01&hasta=2026-09-02'
                       ).get_data(as_text=True)
    assert 'Sin ventas confirmadas en el rango.' in html


def test_fecha_invalida_avisa_y_usa_todo_el_historial(fx):
    html = fx['c'].get('/reportes?desde=abc').get_data(as_text=True)
    assert 'Fecha inválida' in html
    assert f'{MARCA}-C' in html      # rango ignorado: historial completo


def test_rango_invertido_avisa(fx):
    html = fx['c'].get('/reportes?desde=2026-10-12&hasta=2026-10-05'
                       ).get_data(as_text=True)
    assert 'Rango inválido' in html
    assert f'{MARCA}-C' in html      # rango ignorado: historial completo
