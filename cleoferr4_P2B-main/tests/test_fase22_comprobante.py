"""Fase 2.2: emisión D1 del comprobante y rutas del CLIENTE.

Contra la BD real dentro de transacciones con rollback: nada persiste.
La venta 223 del propietario NO se toca (los tests usan ventas sintéticas).
"""
import json
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.comprobantes import EMISOR
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


@pytest.fixture()
def fx(monkeypatch):
    """Venta sintética ONLINE pendiente con pago listo para confirmar."""
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute("SELECT id_producto FROM producto WHERE activo = TRUE ORDER BY id_producto LIMIT 1")
    id_producto = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), 11,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'), %s)
        RETURNING id_venta
    """, (f"test-{_uuid.uuid4().hex[:12]}",))
    id_venta = cur.fetchone()['id_venta']
    cur.execute(
        "INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)"
        " VALUES (%s, %s, 1, 100.00)", (id_venta, id_producto))
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'id_venta': id_venta}
    proxy.rollback()
    raw.close()


def _insertar_pago(fx, detalle_proveedor=None, estado='pendiente'):
    cur = fx['cur']
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela, detalle_proveedor)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                NULL, %s, 100.00, %s, 'simulada', %s::jsonb)
        RETURNING id_pago
    """, (fx['id_venta'], estado, f"sim_test_{_uuid.uuid4().hex[:8]}",
          json.dumps(detalle_proveedor or {})))
    return cur.fetchone()['id_pago']


def _resultado(trx):
    return PagoResultado(id_pago_proveedor=trx, estado=EstadoPago.CONFIRMADO,
                         monto=Decimal('100.00'))


def _comprobante_de(fx):
    fx['cur'].execute("SELECT * FROM comprobante WHERE id_venta = %s", (fx['id_venta'],))
    return fx['cur'].fetchone()


# ── Emisión D1 desde confirmar_pago (misma TXN, idempotente) ────

def test_confirmar_pago_emite_boleta(fx):
    cur = fx['cur']
    id_pago = _insertar_pago(fx, {'tipo_comprobante': 'boleta'})
    cur.execute("SELECT id_transaccion FROM pago WHERE id_pago=%s", (id_pago,))
    trx = cur.fetchone()['id_transaccion']
    res = confirmar_pago(id_pago, _resultado(trx), conn=fx['proxy'])
    assert res['estado'] == 'confirmado'
    comp = _comprobante_de(fx)
    assert comp is not None
    assert comp['serie'] == 'B001'
    assert comp['estado_sunat'] == 'enviado'
    assert comp['referencia_proveedor'] and comp['referencia_proveedor'].startswith('SIM-')
    assert Decimal(str(comp['total'])) == Decimal('100.00')
    assert Decimal(str(comp['subtotal'])) == Decimal('84.75')  # 100/1.18
    assert Decimal(str(comp['igv'])) == Decimal('15.25')


def test_confirmar_pago_emision_idempotente(fx):
    cur = fx['cur']
    id_pago = _insertar_pago(fx, {'tipo_comprobante': 'boleta'})
    cur.execute("SELECT id_transaccion FROM pago WHERE id_pago=%s", (id_pago,))
    trx = cur.fetchone()['id_transaccion']
    confirmar_pago(id_pago, _resultado(trx), conn=fx['proxy'])
    confirmar_pago(id_pago, _resultado(trx), conn=fx['proxy'])  # ya_confirmado
    cur.execute("SELECT COUNT(*) AS n FROM comprobante WHERE id_venta=%s", (fx['id_venta'],))
    assert cur.fetchone()['n'] == 1   # UN comprobante por venta


def test_confirmar_pago_emite_factura_con_ruc(fx):
    cur = fx['cur']
    id_pago = _insertar_pago(fx, {'tipo_comprobante': 'factura', 'ruc': '20605977074'})
    cur.execute("SELECT id_transaccion FROM pago WHERE id_pago=%s", (id_pago,))
    trx = cur.fetchone()['id_transaccion']
    res = confirmar_pago(id_pago, _resultado(trx), conn=fx['proxy'])
    assert res['estado'] == 'confirmado'
    comp = _comprobante_de(fx)
    assert comp['serie'] == 'F001'
    assert comp['ruc'] == '20605977074'
    assert comp['referencia_proveedor'].startswith('SIM-F001-')


def test_emision_con_ruc_invalido_no_rompe_confirmacion(fx):
    """El pago y la venta QUEDAN confirmados; el comprobante queda
    pendiente (SAVEPOINT) y el próximo evento lo reintenta."""
    cur = fx['cur']
    id_pago = _insertar_pago(fx, {'tipo_comprobante': 'factura', 'ruc': '20605977075'})
    cur.execute("SELECT id_transaccion FROM pago WHERE id_pago=%s", (id_pago,))
    trx = cur.fetchone()['id_transaccion']
    res = confirmar_pago(id_pago, _resultado(trx), conn=fx['proxy'])
    assert res['estado'] == 'confirmado'          # el pago no se pierde
    assert _comprobante_de(fx) is None             # comprobante pendiente
    # Corregir el RUC y reintentar (ya_confirmado) lo emite:
    cur.execute("""UPDATE pago SET detalle_proveedor = jsonb_set(detalle_proveedor,
                       '{ruc}', '"20605977074"') WHERE id_pago=%s""", (id_pago,))
    res2 = confirmar_pago(id_pago, _resultado(trx), conn=fx['proxy'])
    assert res2['estado'] == 'ya_confirmado'
    comp = _comprobante_de(fx)
    assert comp is not None and comp['serie'] == 'F001'  # reintento lo emitió


# ── Rutas del CLIENTE (dueño solamente; GET sin escrituras) ─────

def _sesion_cliente(uid=11):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = 'cliente'
    return c


def test_mi_comprobante_dueno_200_con_rotulo(fx):
    cur = fx['cur']
    id_pago = _insertar_pago(fx, {'tipo_comprobante': 'boleta'}, estado='confirmado')
    cur.execute("UPDATE venta SET id_estado_venta ="
                " (SELECT id_estado_venta FROM estado_venta WHERE nombre='confirmado')"
                " WHERE id_venta=%s", (fx['id_venta'],))
    # emitir el comprobante via services (como lo haria confirmar_pago)
    from services.comprobantes import emitir_comprobante_venta
    emitir_comprobante_venta(cur, fx['id_venta'], 'boleta',
                             subtotal=Decimal('84.75'), igv=Decimal('15.25'),
                             total=Decimal('100.00'))
    r = _sesion_cliente(11).get(f'/mi_comprobante/{fx["id_venta"]}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'COMPROBANTE SIMULADO' in html
    assert 'sin validez tributaria' in html
    assert EMISOR['ruc'] in html          # emisor desde .env (no hardcodeado)


def test_mi_comprobante_ajeno_es_403(fx):
    id_pago = _insertar_pago(fx, {'tipo_comprobante': 'boleta'})
    # La venta pertenece al cliente 11; otro cliente (12) debe ver 403
    r = _sesion_cliente(12).get(f'/mi_comprobante/{fx["id_venta"]}')
    assert r.status_code == 403
    # Un vendedor tampoco puede usar la ruta del cliente
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
    assert c.get(f'/mi_comprobante/{fx["id_venta"]}').status_code == 403


def test_mi_comprobante_sin_emision_no_escribe(fx):
    """GET sin comprobante: redirect con aviso, JAMAS crea uno."""
    cur = fx['cur']
    r = _sesion_cliente(11).get(f'/mi_comprobante/{fx["id_venta"]}',
                                follow_redirects=False)
    assert r.status_code == 302
    cur.execute("SELECT COUNT(*) AS n FROM comprobante WHERE id_venta=%s",
                (fx['id_venta'],))
    assert cur.fetchone()['n'] == 0   # no creo nada


def test_mis_comprobantes_solo_lo_suyo(fx):
    cur = fx['cur']
    _insertar_pago(fx, {'tipo_comprobante': 'boleta'}, estado='confirmado')
    from services.comprobantes import emitir_comprobante_venta
    emitir_comprobante_venta(cur, fx['id_venta'], 'boleta',
                             subtotal=Decimal('84.75'), igv=Decimal('15.25'),
                             total=Decimal('100.00'))
    # Una venta de OTRO cliente con comprobante (no debe aparecer)
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), 12,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='entregado'), %s)
        RETURNING id_venta
    """, (f"test-otro-{_uuid.uuid4().hex[:8]}",))
    id_otro = cur.fetchone()['id_venta']
    emitir_comprobante_venta(cur, id_otro, 'boleta',
                              subtotal=Decimal('50.00'), igv=Decimal('9.00'),
                              total=Decimal('59.00'))
    r = _sesion_cliente(11).get('/mis_comprobantes')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'COMPROBANTE SIMULADO' in html
    assert f'/mi_comprobante/{fx["id_venta"]}' in html       # el suyo
    assert f'/mi_comprobante/{id_otro}' not in html           # el ajeno NO


def test_ruta_staff_sin_comprobante_no_crea(fx):
    """El comprobante_venta del STAFF también dejó de crear en el GET."""
    cur = fx['cur']
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
    r = c.get(f'/ventas/{fx["id_venta"]}/comprobante', follow_redirects=False)
    assert r.status_code == 302   # redirect con aviso
    cur.execute("SELECT COUNT(*) AS n FROM comprobante WHERE id_venta=%s",
                (fx['id_venta'],))
    assert cur.fetchone()['n'] == 0
