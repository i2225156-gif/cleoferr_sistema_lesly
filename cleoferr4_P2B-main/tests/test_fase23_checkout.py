"""Fase 2.3 (pieza 3): checkout → pago simulado → confirmar_pago → comprobante.

Contra la BD real dentro de transacciones con rollback: nada persiste.
Producto sintético (S/ 9.99, stock 50) con oferta vigente del 15%:
descuento 1.50 por unidad, precio final 8.49. El flujo completo pasa
por las rutas reales (POST /carrito/confirmar_v2) y por confirmar_pago,
la única puerta a 'confirmado'.
"""
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.pagos import confirmar_pago
from services.pasarela.base import EstadoPago, PagoResultado


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


def _sesion_cliente():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 11
        s['rol'] = 'cliente'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    """Producto sintético 9.99 con oferta vigente 15% (dcto 1.50/unidad)."""
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 9.99, 50)"
        " RETURNING id_producto, precio", (f"test23c-{_uuid.uuid4().hex[:10]}",))
    prod = cur.fetchone()
    cur.execute("""
        INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio, fecha_fin)
        VALUES (%s, 'porcentaje', 15,
                NOW() - INTERVAL '1 hour', NOW() + INTERVAL '1 day')
        RETURNING id_oferta
    """, (prod['id_producto'],))
    id_oferta = cur.fetchone()['id_oferta']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'client': _sesion_cliente(), 'cur': cur, 'proxy': proxy,
           'id_producto': prod['id_producto'], 'precio': prod['precio'],
           'id_oferta': id_oferta}
    proxy.rollback()
    raw.close()


def _checkout(fx, items, extra=None):
    datos = {'items': items, 'metodo_pago': 'efectivo',
             'tipo_comprobante': 'boleta_simple', 'num_operacion': '',
             'csrf_token': 'token-test'}
    if extra:
        datos.update(extra)
    return fx['client'].post('/carrito/confirmar_v2', json=datos)


def _pago_de(fx, id_venta):
    fx['cur'].execute(
        "SELECT id_pago, monto, id_transaccion, estado FROM pago WHERE id_venta = %s",
        (id_venta,))
    return fx['cur'].fetchone()


def _resultado(fx, id_venta, monto):
    pago = _pago_de(fx, id_venta)
    return pago, PagoResultado(id_pago_proveedor=pago['id_transaccion'],
                              estado=EstadoPago.CONFIRMADO, monto=monto)


# ── Flujo completo con oferta vigente ─────────────────────────────

def test_flujo_completo_con_oferta_vigente(fx):
    r = _checkout(fx, [{'id_producto': fx['id_producto'], 'cantidad': 3}])
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is True
    cur, id_venta = fx['cur'], d['id_pedido']

    # 1) El descuento quedó CONGELADO en detalle_venta
    cur.execute("""SELECT cantidad, precio_unitario, id_oferta, descuento_unitario
                     FROM detalle_venta WHERE id_venta = %s""", (id_venta,))
    det = cur.fetchone()
    assert Decimal(str(det['precio_unitario'])) == Decimal('9.99')
    assert det['id_oferta'] == fx['id_oferta']
    assert Decimal(str(det['descuento_unitario'])) == Decimal('1.50')

    # 2) pago.monto es el total NETO (3 x 8.49), no el bruto
    pago = _pago_de(fx, id_venta)
    assert Decimal(str(pago['monto'])) == Decimal('25.47')
    assert pago['estado'] == 'pendiente'

    # 3) confirmar_pago cuadra con el neto y CONFIRMA + emite comprobante
    pago, resultado = _resultado(fx, id_venta, Decimal('25.47'))
    res = confirmar_pago(pago['id_pago'], resultado, conn=fx['proxy'])
    assert res['estado'] == 'confirmado'
    cur.execute("SELECT * FROM comprobante WHERE id_venta = %s", (id_venta,))
    comp = cur.fetchone()
    assert Decimal(str(comp['total'])) == Decimal('25.47')      # descontado
    assert Decimal(str(comp['subtotal'])) == Decimal('21.58')   # 25.47/1.18
    assert Decimal(str(comp['igv'])) == Decimal('3.89')

    # 4) El comprobante RENDERIZADO refleja el descuento (fila solo si > 0)
    html = fx['client'].get(f'/mi_comprobante/{id_venta}').get_data(as_text=True)
    assert 'Descuento' in html and '4.50' in html     # 3 x 1.50
    assert '8.49' in html                             # precio final por unidad
    assert '25.47' in html                           # TOTAL descontado


def test_oferta_vence_entre_checkout_y_pago(fx):
    """La oferta vence tras el checkout: el monto CONGELADO sigue cuadrando."""
    r = _checkout(fx, [{'id_producto': fx['id_producto'], 'cantidad': 1}])
    d = r.get_json()
    assert d['ok'] is True
    cur, id_venta = fx['cur'], d['id_pedido']
    # La oferta queda fuera de vigencia ANTES de confirmar el pago
    cur.execute("UPDATE oferta SET fecha_fin = NOW() - INTERVAL '1 minute'"
                " WHERE id_oferta = %s", (fx['id_oferta'],))
    pago = _pago_de(fx, id_venta)
    assert Decimal(str(pago['monto'])) == Decimal('8.49')   # congelado al comprar
    res = confirmar_pago(pago['id_pago'],
                         PagoResultado(id_pago_proveedor=pago['id_transaccion'],
                                       estado=EstadoPago.CONFIRMADO,
                                       monto=Decimal('8.49')),
                         conn=fx['proxy'])
    assert res['estado'] == 'confirmado'


def test_precio_manipulado_en_post_se_ignora(fx):
    """El POST trae precio 0.01: el servidor usa el precio oficial de la BD."""
    r = _checkout(fx, [{'id_producto': fx['id_producto'], 'cantidad': 2,
                        'precio': 0.01}])
    d = r.get_json()
    assert d['ok'] is True
    cur, id_venta = fx['cur'], d['id_pedido']
    cur.execute("""SELECT precio_unitario, descuento_unitario
                     FROM detalle_venta WHERE id_venta = %s""", (id_venta,))
    det = cur.fetchone()
    assert Decimal(str(det['precio_unitario'])) == Decimal('9.99')
    assert Decimal(str(det['descuento_unitario'])) == Decimal('1.50')
    assert Decimal(str(_pago_de(fx, id_venta)['monto'])) == Decimal('16.98')  # 2 x 8.49


def test_monto_sin_descuento_rechazado_por_confirmar_pago(fx):
    """pago.monto bruto (sin dcto) no cuadra con el total neto: invalido."""
    r = _checkout(fx, [{'id_producto': fx['id_producto'], 'cantidad': 1}])
    d = r.get_json()
    assert d['ok'] is True
    cur, id_venta = fx['cur'], d['id_pedido']
    # Simula un pago registrado con el monto BRUTO (error/bug/manipulación)
    cur.execute("UPDATE pago SET monto = 9.99 WHERE id_venta = %s", (id_venta,))
    pago, resultado = _resultado(fx, id_venta, Decimal('9.99'))
    res = confirmar_pago(pago['id_pago'], resultado, conn=fx['proxy'])
    assert res['estado'] == 'invalido'
    assert 'total recalculado' in res['motivo']
    # Nada se confirmó ni se emitió
    assert _pago_de(fx, id_venta)['estado'] == 'pendiente'
    cur.execute("SELECT COUNT(*) AS n FROM comprobante WHERE id_venta = %s",
                (id_venta,))
    assert cur.fetchone()['n'] == 0


def test_venta_sin_oferta_sin_cambios(fx):
    """Sin oferta: dcto 0 y el comportamiento previo queda intacto."""
    cur = fx['cur']
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 100.00, 10)"
        " RETURNING id_producto", (f"test23c-{_uuid.uuid4().hex[:10]}",))
    id_p2 = cur.fetchone()['id_producto']
    r = _checkout(fx, [{'id_producto': id_p2, 'cantidad': 1}])
    d = r.get_json()
    assert d['ok'] is True
    id_venta = d['id_pedido']
    cur.execute("""SELECT precio_unitario, descuento_unitario, id_oferta
                     FROM detalle_venta WHERE id_venta = %s""", (id_venta,))
    det = cur.fetchone()
    assert Decimal(str(det['precio_unitario'])) == Decimal('100.00')
    assert Decimal(str(det['descuento_unitario'])) == Decimal('0')
    assert det['id_oferta'] is None
    assert Decimal(str(_pago_de(fx, id_venta)['monto'])) == Decimal('100.00')
    pago, resultado = _resultado(fx, id_venta, Decimal('100.00'))
    res = confirmar_pago(pago['id_pago'], resultado, conn=fx['proxy'])
    assert res['estado'] == 'confirmado'
    cur.execute("SELECT total, subtotal, igv FROM comprobante WHERE id_venta = %s",
                (id_venta,))
    comp = cur.fetchone()
    assert Decimal(str(comp['total'])) == Decimal('100.00')
    assert Decimal(str(comp['subtotal'])) == Decimal('84.75')
    assert Decimal(str(comp['igv'])) == Decimal('15.25')
    # Sin descuento no aparece la fila en el comprobante renderizado
    html = fx['client'].get(f'/mi_comprobante/{id_venta}').get_data(as_text=True)
    assert 'Descuento' not in html
