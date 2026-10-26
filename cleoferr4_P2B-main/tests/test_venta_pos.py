"""POS (POST /ventas/nueva): contrato del descuento de stock (C2).

Contra la BD real dentro de transacciones con rollback: nada persiste.
La venta local usa la MISMA regla de descuento que el checkout online
(UPDATE con guarda stock >= cantidad): no hay sobreventa secuencial ni
por líneas duplicadas, y el camino feliz deja venta entregada + pago
confirmado + stock + movimiento + ingreso de caja + comprobante.
"""
import re
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


class ConnProxy:
    """commit() no-op: la ruta cree que commitea; el test hace rollback."""

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


@pytest.fixture()
def fx(monkeypatch):
    suf = _uuid.uuid4().hex[:10]
    raw_a = db_supabase.engine_auth.raw_connection()
    raw_t = db_supabase.engine_tienda.raw_connection()
    proxy_a, proxy_t = ConnProxy(raw_a), ConnProxy(raw_t)
    cur_a, cur_t = proxy_a.cursor(dictionary=True), proxy_t.cursor(dictionary=True)
    cur_a.execute("""
        INSERT INTO usuario (email, contrasena, id_rol, nombres, activo)
        VALUES (%s, 'no-importa', (SELECT id_rol FROM rol WHERE nombre='vendedor'),
                'Vendedor POS', TRUE)
        RETURNING id_usuario
    """, (f"vend-pos-{suf}@invalido",))
    id_vendedor = cur_a.fetchone()['id_usuario']
    cur_a.execute("""
        INSERT INTO cliente (nombre, apellido, email, telefono, direccion,
                             contrasena, activo, id_tipo_documento, nro_documento)
        VALUES ('Pos', 'Cliente Prueba', %s, '987654321', 'Calle Uno',
                'no-importa', TRUE, 1, %s)
        RETURNING id_cliente
    """, (f"cli-pos-{suf}@invalido", f"{_uuid.uuid4().int % 100000000:08d}"))
    id_cliente = cur_a.fetchone()['id_cliente']
    cur_t.execute("""
        INSERT INTO producto (nombre, precio, stock, activo)
        VALUES (%s, 12.50, 10, TRUE) RETURNING id_producto
    """, (f"zzq-pos-{suf}",))
    id_producto = cur_t.fetchone()['id_producto']
    # caja: la real abierta si existe; si no, una sintética en la TXN
    cur_t.execute("SELECT id_caja FROM caja WHERE estado = 'abierta' LIMIT 1")
    fila_caja = cur_t.fetchone()
    id_caja = fila_caja['id_caja'] if fila_caja else None
    if id_caja is None:
        cur_t.execute("""
            INSERT INTO caja (id_usuario_apertura, monto_apertura, estado)
            VALUES (%s, 100.00, 'abierta') RETURNING id_caja
        """, (id_vendedor,))
        id_caja = cur_t.fetchone()['id_caja']
    # producto real (menor stock >= 1) para las pruebas de rollback: al
    # revertir la ruta su transacción, los sintéticos desaparecen del
    # proxy; los reales siguen visibles para verificar que nada quedó
    # descontado (excluido el sintético de este fixture).
    cur_t.execute("""SELECT id_producto, nombre, stock FROM producto
                      WHERE activo = TRUE AND stock >= 1
                        AND nombre NOT LIKE %s
                      ORDER BY stock ASC, id_producto LIMIT 1""",
                  ("zzq-pos-%",))
    real = cur_t.fetchone()
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy_a)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy_t)
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = id_vendedor
        s['rol'] = 'vendedor'
        s['nombre'] = 'Vendedor POS'
        s['_csrf_token'] = 'token-test'
    yield {'client': c, 'cur': cur_t, 'id_cliente': id_cliente,
           'id_producto': id_producto, 'id_caja': id_caja, 'suf': suf,
           'real': real}
    proxy_a.rollback()
    proxy_t.rollback()
    raw_a.close()
    raw_t.close()


def _post_venta(fx, lineas, metodo='efectivo'):
    c = fx['client']
    c.get('/ventas/nueva')
    with c.session_transaction() as s:
        token = s.get('venta_token')
    return c.post('/ventas/nueva', data={
        'venta_token': token,
        'id_cliente': str(fx['id_cliente']),
        'tipo_venta': 'local',
        'metodo_pago': metodo,
        'id_producto[]': [str(pid) for pid, _ in lineas],
        'cantidad[]': [str(cant) for _, cant in lineas],
        'comp_tipo': 'boleta',
    }, follow_redirects=True)


def _stock(fx):
    fx['cur'].execute("SELECT stock FROM producto WHERE id_producto = %s",
                      (fx['id_producto'],))
    return fx['cur'].fetchone()['stock']


def _n_ventas(fx):
    fx['cur'].execute("SELECT COUNT(*) AS n FROM venta WHERE id_cliente = %s",
                      (fx['id_cliente'],))
    return fx['cur'].fetchone()['n']


def _venta_id(fx, respuesta):
    m = re.search(r'Venta local #(\d+)', respuesta.get_data(as_text=True))
    if m:
        return int(m.group(1))
    fx['cur'].execute("""SELECT v.id_venta FROM venta v WHERE v.id_cliente = %s
                         ORDER BY v.id_venta DESC LIMIT 1""", (fx['id_cliente'],))
    fila = fx['cur'].fetchone()
    return fila['id_venta'] if fila else None


def test_venta_local_camino_feliz_efecto_real(fx):
    cur = fx['cur']
    r = _post_venta(fx, [(fx['id_producto'], 2)])
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'registrada correctamente' in html
    id_venta = _venta_id(fx, r)
    assert id_venta is not None
    # venta entregada, tipo local
    cur.execute("""SELECT ev.nombre AS estado, tv.nombre AS tipo
                     FROM venta v JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
                     JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
                    WHERE v.id_venta = %s""", (id_venta,))
    v = cur.fetchone()
    assert v['estado'] == 'entregado' and v['tipo'] == 'local'
    # detalle con precio oficial
    cur.execute("SELECT cantidad, precio_unitario FROM detalle_venta WHERE id_venta = %s",
                (id_venta,))
    d = cur.fetchone()
    assert d['cantidad'] == 2 and float(d['precio_unitario']) == 12.50
    # stock descontado con la guarda (10 pasa a 8) y movimiento de salida
    assert _stock(fx) == 8
    cur.execute("""SELECT tipo, cantidad, stock_resultante FROM inventario_movimiento
                    WHERE observacion = %s""", (f'Venta local #{id_venta}',))
    mov = cur.fetchone()
    assert mov['tipo'] == 'salida' and mov['cantidad'] == 2 and mov['stock_resultante'] == 8
    # pago confirmado en efectivo y asociado a la caja del turno
    cur.execute("""SELECT p.estado, tp.nombre AS metodo, p.id_caja
                     FROM pago p JOIN tipo_pago tp ON tp.id_tipo_pago = p.id_tipo_pago
                    WHERE p.id_venta = %s""", (id_venta,))
    pago = cur.fetchone()
    assert pago['estado'] == 'confirmado' and pago['metodo'] == 'efectivo'
    assert pago['id_caja'] == fx['id_caja']
    # ingreso en caja por el total (2 x 12.50 = 25.00)
    cur.execute("""SELECT tipo, monto FROM movimiento_caja
                    WHERE concepto = %s""", (f'Venta local #{id_venta}',))
    mc = cur.fetchone()
    assert mc['tipo'] == 'ingreso' and float(mc['monto']) == 25.00
    # comprobante de la venta (boleta)
    cur.execute("SELECT numero, serie, total FROM comprobante WHERE id_venta = %s",
                (id_venta,))
    comp = cur.fetchone()
    assert comp['serie'] == 'B001' and comp['numero'].startswith('B')
    assert float(comp['total']) == 25.00


def test_venta_local_stock_insuficiente_rechazada_y_no_escribe(fx):
    """Sobre un producto REAL: pedir más de lo que hay da flash claro y
    NADA escrito (rollback de la ruta; verificado en datos comprometidos)."""
    if not fx['real']:
        pytest.skip('sin productos reales con stock')
    cur = fx['cur']
    pid, stock = fx['real']['id_producto'], fx['real']['stock']
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE id_producto = %s",
                (pid,))
    mov_antes = cur.fetchone()['n']
    r = _post_venta(fx, [(pid, stock + 1)])
    html = r.get_data(as_text=True)
    assert 'Stock insuficiente' in html
    assert f'disponible: {stock}' in html
    assert _n_ventas(fx) == 0
    cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (pid,))
    assert cur.fetchone()['stock'] == stock
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE id_producto = %s",
                (pid,))
    assert cur.fetchone()['n'] == mov_antes


def test_venta_local_lineas_duplicadas_no_sobrevenen(fx):
    """Dos líneas que agotan el stock de un producto REAL: la segunda no
    encuentra stock y TODO se revierte (ni venta, ni detalle, ni
    descuento)."""
    if not fx['real']:
        pytest.skip('sin productos reales con stock')
    cur = fx['cur']
    pid, stock = fx['real']['id_producto'], fx['real']['stock']
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE id_producto = %s",
                (pid,))
    mov_antes = cur.fetchone()['n']
    r = _post_venta(fx, [(pid, stock), (pid, stock)])
    html = r.get_data(as_text=True)
    assert 'Stock insuficiente' in html
    assert _n_ventas(fx) == 0
    cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (pid,))
    assert cur.fetchone()['stock'] == stock
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE id_producto = %s",
                (pid,))
    assert cur.fetchone()['n'] == mov_antes
