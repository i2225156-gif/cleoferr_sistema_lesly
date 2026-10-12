"""Fase B: CLIENTES VARIOS (umbral configurable para boletas sin DNI).

Contra la BD real dentro de transacciones con rollback: nada persiste.
El umbral es una regla de NEGOCIO marcada 'a verificar' (no confirmada
como exigencia SUNAT vigente; ver .env.example).
"""
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.comprobantes import (UMBRAL_BOLETA_SIN_DOC,
                                    boleta_necesita_documento,
                                    emitir_comprobante_del_pago)
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


# ── La regla del umbral (sin BD) ─────────────────────────────────

def test_umbral_leido_del_entorno():
    assert UMBRAL_BOLETA_SIN_DOC == Decimal('700')   # default marcado 'a verificar'


def test_boleta_necesita_documento():
    # sin documento + debajo del umbral -> NO lo exige
    assert boleta_necesita_documento(100, None) is False
    assert boleta_necesita_documento(Decimal('699.99'), '') is False
    # sin documento + sobre el umbral -> SÍ lo exige
    assert boleta_necesita_documento(700.01, None) is True
    assert boleta_necesita_documento(1000, '') is True
    # con documento -> NO lo exige (no importa el monto)
    assert boleta_necesita_documento(5000, '12345678') is False


# ── Checkout: rechazo sobre el umbral sin DNI ─────────────────────

def _sesion_cliente():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 11
        s['rol'] = 'cliente'
        s['_csrf_token'] = 'token-test'
    return c


def _crear_pedido(fx, items, extra=None):
    datos = {
        'items': items,
        'metodo_pago': 'tarjeta',
        'tipo_comprobante': 'boleta_simple',
        'num_operacion': '',
        'csrf_token': 'token-test',
    }
    if extra:
        datos.update(extra)
    return fx['client'].post('/carrito/confirmar_v2', json=datos)


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute("""SELECT id_producto, precio, stock FROM producto
                    WHERE activo = TRUE ORDER BY id_producto LIMIT 1""")
    prod = cur.fetchone()
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'client': _sesion_cliente(), 'cur': cur, 'proxy': proxy,
           'id_producto': prod['id_producto'], 'precio': prod['precio'],
           'monkeypatch': monkeypatch}
    proxy.rollback()
    raw.close()


def _umbral_bajo(monkeypatch):
    """Umbral PEQUEÑO: cualquier producto lo supera (para test de rechazo)."""
    monkeypatch.setattr(modulo_app, 'UMBRAL_BOLETA_SIN_DOC', Decimal('10'))
    monkeypatch.setattr('services.comprobantes.UMBRAL_BOLETA_SIN_DOC', Decimal('10'))


def _umbral_alto(monkeypatch):
    """Umbral GRANDE: ningún producto de prueba lo supera (para test de aceptación)."""
    monkeypatch.setattr(modulo_app, 'UMBRAL_BOLETA_SIN_DOC', Decimal('99999'))
    monkeypatch.setattr('services.comprobantes.UMBRAL_BOLETA_SIN_DOC', Decimal('99999'))


def test_checkout_boleta_sobre_umbral_sin_dni_rechaza(fx):
    _umbral_bajo(fx['monkeypatch'])
    r = _crear_pedido(fx, [{'id_producto': fx['id_producto'], 'cantidad': 1}])
    assert r.status_code == 400
    cuerpo = r.get_json()
    assert cuerpo['ok'] is False
    assert 'DNI' in cuerpo['error'] or 'documento' in cuerpo['error']


def test_checkout_boleta_sobre_umbral_con_dni_acepta(fx):
    _umbral_bajo(fx['monkeypatch'])
    r = _crear_pedido(fx, [{'id_producto': fx['id_producto'], 'cantidad': 1}],
                       extra={'dni': '12345678'})
    assert r.status_code == 200
    assert r.get_json()['ok'] is True


def test_checkout_boleta_bajo_umbral_sin_dni_acepta(fx):
    _umbral_alto(fx['monkeypatch'])
    r = _crear_pedido(fx, [{'id_producto': fx['id_producto'], 'cantidad': 1}])
    assert r.status_code == 200
    assert r.get_json()['ok'] is True


# ── Emisión: CLIENTES VARIOS en boleta sin documento ─────────────

def test_emision_marca_clientes_varios_sin_documento(fx):
    """Boleta sin DNI (bajo umbral) -> la emisión completa sin error."""
    _umbral_alto(fx['monkeypatch'])
    cur = fx['cur']
    r = _crear_pedido(fx, [{'id_producto': fx['id_producto'], 'cantidad': 1}])
    d = r.get_json()
    assert d['ok'] is True
    # confirmar el pago (via confirmar_pago con la conexión del proxy)
    cur.execute("SELECT id_pago, id_transaccion FROM pago WHERE id_venta=%s",
                (d['id_pedido'],))
    fila = cur.fetchone()
    trx = fila['id_transaccion']
    cur.execute("""SELECT SUM(cantidad * precio_unitario) AS t
                     FROM detalle_venta WHERE id_venta=%s""", (d['id_pedido'],))
    total = cur.fetchone()['t']
    confirmar_pago(fila['id_pago'],
                   PagoResultado(id_pago_proveedor=trx, estado=EstadoPago.CONFIRMADO,
                                 monto=total),
                   conn=fx['proxy'])
    # el comprobante se emitió
    cur.execute("SELECT * FROM comprobante WHERE id_venta=%s", (d['id_pedido'],))
    comp = cur.fetchone()
    assert comp is not None
    assert comp['serie'] == 'B001'
    assert comp['ruc'] is None   # sin documento del cliente
