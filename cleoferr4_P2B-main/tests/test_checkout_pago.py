"""Tramo 2: checkout→crear_pago y pantalla de pago simulado.

Ruta REAL con test client; la conexión a BD es un proxy con commit() no-op
dentro de una transacción con rollback: nada persiste. La sesión es del
cliente 11 (id real; sin datos personales impresos). El monto del pago debe
salir de detalle_venta, nunca del navegador (se envía un precio falsificado
y el servidor debe ignorarlo).
"""
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.pasarela.simulada import TARJETA_APROBADA, TARJETA_RECHAZADA


class ConnProxy:
    """commit() no-op: el route cree que commitea; el test hace rollback."""

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
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute("""SELECT id_producto, precio FROM producto
                    WHERE activo = TRUE AND stock >= 1
                    ORDER BY id_producto LIMIT 1""")
    prod = cur.fetchone()
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    client = app.test_client()
    with client.session_transaction() as s:
        s['usuario_id'] = 11
        s['rol'] = 'cliente'
        s['nombre'] = 'Cliente'
        s['_csrf_token'] = 'token-test'
    yield {'client': client, 'cur': cur, 'proxy': proxy,
           'id_producto': prod['id_producto'], 'precio': prod['precio']}
    proxy.rollback()
    raw.close()


def _crear_pedido(fx):
    r = fx['client'].post('/carrito/confirmar_v2', json={
        # precio falsificado: el servidor debe usar el precio oficial de la BD
        'items': [{'id_producto': fx['id_producto'], 'cantidad': 1, 'precio': 0.01}],
        'metodo_pago': 'tarjeta',
        'tipo_comprobante': 'boleta_simple',
        'num_operacion': '',
        'csrf_token': 'token-test',
    })
    assert r.status_code == 200
    return r.get_json()


def _pago_de(fx, id_transaccion):
    cur = fx['cur']
    cur.execute("""SELECT p.*, v.id_venta, ev.nombre AS venta_estado
                     FROM pago p JOIN venta v ON v.id_venta = p.id_venta
                     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
                    WHERE p.id_transaccion = %s""", (id_transaccion,))
    return cur.fetchone()


def test_checkout_crea_pago_pendiente_con_monto_y_transaccion(fx):
    legacy_antes = None
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM pago WHERE id_transaccion IS NULL AND estado='pendiente'")
    legacy_antes = cur.fetchone()['n']

    d = _crear_pedido(fx)
    assert d['ok'] is True
    assert d['checkout_url'] == f"/pago/simulado/sim_venta-{d['id_pedido']}"
    pago = _pago_de(fx, f"sim_venta-{d['id_pedido']}")
    assert pago['estado'] == 'pendiente'
    assert pago['pasarela'] == 'simulada'
    # Monto = precio OFICIAL de la BD (no el 0.01 falsificado del navegador)
    assert Decimal(str(pago['monto'])) == Decimal(str(fx['precio']))
    assert pago['venta_estado'] == 'pendiente'
    cur.execute("SELECT detalle_proveedor->>'checkout_url' AS url FROM pago WHERE id_pago=%s",
                (pago['id_pago'],))
    assert cur.fetchone()['url'] == d['checkout_url']
    # D1: el comprobante sigue creándose con el pedido en 2.1
    cur.execute("SELECT COUNT(*) AS n FROM comprobante WHERE id_venta=%s", (d['id_pedido'],))
    assert cur.fetchone()['n'] == 1
    # Los 16 pagos legacy (sin id_transaccion) no se tocan
    cur.execute("SELECT COUNT(*) AS n FROM pago WHERE id_transaccion IS NULL AND estado='pendiente'")
    assert cur.fetchone()['n'] == legacy_antes


def test_pantalla_visible_para_el_dueno_con_tarjetas_de_prueba(fx):
    d = _crear_pedido(fx)
    trx = f"sim_venta-{d['id_pedido']}"
    r = fx['client'].get(f'/pago/simulado/{trx}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'PAGO SIMULADO' in html                      # bandera visible
    assert 'sin cobro real' in html
    assert TARJETA_APROBADA in html                     # sincronizadas con simulada.py
    assert TARJETA_RECHAZADA in html
    assert 'X-CSRFToken' in html                        # el POST viaja con CSRF


def test_pantalla_de_un_ajeno_es_403_para_cliente(fx):
    d = _crear_pedido(fx)
    otro = app.test_client()
    with otro.session_transaction() as s:
        s['usuario_id'] = 12
        s['rol'] = 'cliente'
        s['_csrf_token'] = 'token-test'
    r = otro.get(f"/pago/simulado/sim_venta-{d['id_pedido']}")
    assert r.status_code == 403


def test_pantalla_deshabilitada_si_modo_no_simulado(fx, monkeypatch):
    d = _crear_pedido(fx)
    monkeypatch.setattr(modulo_app._PASARELA, 'modo', 'otro')
    r = fx['client'].get(f"/pago/simulado/sim_venta-{d['id_pedido']}")
    assert r.status_code == 302


def test_procesar_aprobado_confirma_por_la_unica_puerta(fx):
    d = _crear_pedido(fx)
    trx = f"sim_venta-{d['id_pedido']}"
    r = fx['client'].post(f'/pago/simulado/{trx}/procesar',
                           json={'resultado': 'aprobado', 'csrf_token': 'token-test'})
    cuerpo = r.get_json()
    assert r.status_code == 200
    assert cuerpo['estado'] == 'confirmado'
    pago = _pago_de(fx, trx)
    assert pago['estado'] == 'confirmado'
    assert pago['venta_estado'] == 'confirmado'
    cur = fx['cur']
    cur.execute("""SELECT procesado_en, estado_resultante FROM pago_evento
                    WHERE id_evento_proveedor = %s""", (f'checkout-{trx}-aprobado',))
    ev = cur.fetchone()
    assert ev['estado_resultante'] == 'confirmado'
    assert ev['procesado_en'] is not None


def test_procesar_rechazado_no_toca_la_venta(fx):
    d = _crear_pedido(fx)
    trx = f"sim_venta-{d['id_pedido']}"
    r = fx['client'].post(f'/pago/simulado/{trx}/procesar',
                           json={'resultado': 'rechazado', 'csrf_token': 'token-test'})
    assert r.get_json()['estado'] == 'rechazado'
    pago = _pago_de(fx, trx)
    assert pago['estado'] == 'rechazado'
    assert pago['venta_estado'] == 'pendiente'


def test_procesar_ignora_datos_de_tarjeta_si_llegaran(fx):
    d = _crear_pedido(fx)
    trx = f"sim_venta-{d['id_pedido']}"
    r = fx['client'].post(f'/pago/simulado/{trx}/procesar', json={
        'resultado': 'aprobado', 'csrf_token': 'token-test',
        'numero_tarjeta': TARJETA_APROBADA, 'cvv': '123', 'vencimiento': '12/30',
    })
    assert r.get_json()['estado'] == 'confirmado'
    cur = fx['cur']
    cur.execute("""SELECT NOT (detalle ? 'numero_tarjeta') AS sin_tarjeta,
                          NOT (detalle ? 'cvv') AS sin_cvv
                     FROM pago_evento WHERE id_evento_proveedor = %s""",
                (f'checkout-{trx}-aprobado',))
    fila = cur.fetchone()
    assert fila['sin_tarjeta'] and fila['sin_cvv']


def test_procesar_sin_csrf_es_400(fx):
    d = _crear_pedido(fx)
    trx = f"sim_venta-{d['id_pedido']}"
    r = fx['client'].post(f'/pago/simulado/{trx}/procesar',
                           json={'resultado': 'aprobado'})
    assert r.status_code == 400


def test_procesar_doble_clic_no_duplica_nada(fx):
    d = _crear_pedido(fx)
    trx = f"sim_venta-{d['id_pedido']}"
    r1 = fx['client'].post(f'/pago/simulado/{trx}/procesar',
                           json={'resultado': 'aprobado', 'csrf_token': 'token-test'})
    r2 = fx['client'].post(f'/pago/simulado/{trx}/procesar',
                           json={'resultado': 'aprobado', 'csrf_token': 'token-test'})
    # El checkout ve el estado antes de reclamar el evento: el segundo
    # clic no reproduce nada (el 'repetido' por id de evento aplica al
    # webhook, testeado en test_tramo2_webhook).
    assert r1.get_json()['estado'] == 'confirmado'
    assert r2.get_json()['estado'] in ('repetido', 'ya_procesado')
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM pago_evento WHERE id_evento_proveedor = %s",
                (f'checkout-{trx}-aprobado',))
    assert cur.fetchone()['n'] == 1
    assert _pago_de(fx, trx)['estado'] == 'confirmado'
