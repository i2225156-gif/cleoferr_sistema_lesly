"""Tramo 2 (2b): webhook /webhooks/pago/simulada.

La ruta REAL se ejercita con el test client; la conexión a BD se sustituye
por un proxy con commit() no-op dentro de una transacción con rollback:
nada persiste. La firma usa el secreto real del .env (nunca se imprime).
"""
import hashlib
import hmac
import json
import os
import time
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.pasarela import EstadoPago
from services.pasarela.simulada import FIRMA_CABECERA, PasarelaSimulada


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
    cur.execute("SELECT id_producto FROM producto ORDER BY id_producto LIMIT 1")
    id_producto = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), NULL,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'), %s)
        RETURNING id_venta
    """, (f"test-{_uuid.uuid4().hex[:12]}",))
    id_venta = cur.fetchone()['id_venta']
    cur.execute(
        "INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)"
        " VALUES (%s, %s, 2, 50.00)", (id_venta, id_producto))
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                NULL, 'pendiente', 100.00, %s, 'simulada')
        RETURNING id_pago, id_transaccion
    """, (id_venta, f"sim_test_{_uuid.uuid4().hex[:10]}"))
    fila = cur.fetchone()
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'client': app.test_client(), 'cur': cur, 'proxy': proxy,
           'id_venta': id_venta, 'id_pago': fila['id_pago'],
           'id_transaccion': fila['id_transaccion']}
    proxy.rollback()
    raw.close()


def _firmar(trx, estado, monto):
    p = PasarelaSimulada(secreto_webhook=os.environ['PAGO_SIMULADA_WEBHOOK_SECRETO'])
    return p.firmar_evento(trx, estado, monto)


def _firmar_manual(datos):
    secreto = os.environ['PAGO_SIMULADA_WEBHOOK_SECRETO'].encode()
    payload = json.dumps(datos, separators=(',', ':')).encode('utf-8')
    firma = hmac.new(secreto, payload, hashlib.sha256).hexdigest()
    return payload, {FIRMA_CABECERA: firma}


def _estado(fx, tabla, columna, clave, valor_clave):
    cur = fx['cur']
    cur.execute(f"SELECT {columna} AS v FROM {tabla} WHERE {clave} = %s", (valor_clave,))
    return cur.fetchone()['v']


def test_firma_valida_confirma(fx):
    payload, headers = _firmar(fx['id_transaccion'], EstadoPago.CONFIRMADO, Decimal('100.00'))
    r = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['estado'] == 'confirmado' and cuerpo['ok'] is True
    assert _estado(fx, 'pago', 'estado', 'id_pago', fx['id_pago']) == 'confirmado'
    cur = fx['cur']
    cur.execute("""SELECT ev.nombre AS e FROM venta v JOIN estado_venta ev
                     ON ev.id_estado_venta = v.id_estado_venta WHERE v.id_venta = %s""",
                (fx['id_venta'],))
    assert cur.fetchone()['e'] == 'confirmado'
    cur.execute("""SELECT procesado_en FROM pago_evento
                    WHERE id_pago = %s ORDER BY id_pago_evento DESC LIMIT 1""",
                (fx['id_pago'],))
    assert cur.fetchone()['procesado_en'] is not None


def test_firma_invalida_400_y_queda_registrada(fx):
    payload, headers = _firmar(fx['id_transaccion'], EstadoPago.CONFIRMADO, Decimal('100.00'))
    r = fx['client'].post('/webhooks/pago/simulada', data=payload,
                          headers={FIRMA_CABECERA: 'falsificada'})
    assert r.status_code == 400
    assert 'firma' in r.get_json()['error']
    assert _estado(fx, 'pago', 'estado', 'id_pago', fx['id_pago']) == 'pendiente'
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM pago_evento WHERE firma_valida = FALSE")
    assert cur.fetchone()['n'] == 1


def test_firma_vencida_400(fx):
    payload, headers = _firmar_manual({
        'id_evento': 'evt-vencido-1', 'id_pago': fx['id_transaccion'],
        'tipo': 'pago_actualizado', 'estado': 'confirmado',
        'monto': '100.00', 'timestamp': time.time() - 3600,
    })
    r = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    assert r.status_code == 400
    assert 'tolerancia' in r.get_json()['error']
    assert _estado(fx, 'pago', 'estado', 'id_pago', fx['id_pago']) == 'pendiente'
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM pago_evento WHERE dentro_de_tolerancia = FALSE")
    assert cur.fetchone()['n'] == 1


def test_evento_repetido_no_duplica(fx):
    payload, headers = _firmar(fx['id_transaccion'], EstadoPago.CONFIRMADO, Decimal('100.00'))
    r1 = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    assert r1.get_json()['estado'] == 'confirmado'
    r2 = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    assert r2.status_code == 200
    assert r2.get_json()['estado'] == 'repetido'
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM pago_evento WHERE id_pago = %s", (fx['id_pago'],))
    assert cur.fetchone()['n'] == 1  # un solo evento registrado
    assert _estado(fx, 'pago', 'estado', 'id_pago', fx['id_pago']) == 'confirmado'


def test_monto_distinto_rechazado_por_validacion(fx):
    payload, headers = _firmar(fx['id_transaccion'], EstadoPago.CONFIRMADO, Decimal('999.00'))
    r = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['estado'] == 'invalido' and cuerpo['ok'] is False
    assert _estado(fx, 'pago', 'estado', 'id_pago', fx['id_pago']) == 'pendiente'
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM auditoria WHERE accion = 'validacion_rechazada'")
    assert cur.fetchone()['n'] == 1


def test_estado_desconocido_ignorado_y_nunca_en_pago(fx):
    payload, headers = _firmar_manual({
        'id_evento': 'evt-raro-1', 'id_pago': fx['id_transaccion'],
        'tipo': 'pago_actualizado', 'estado': 'estado_inventado',
        'monto': '100.00', 'timestamp': time.time(),
    })
    r = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    assert r.status_code == 200
    assert r.get_json()['estado'] == 'ignorado'
    assert _estado(fx, 'pago', 'estado', 'id_pago', fx['id_pago']) == 'pendiente'
    cur = fx['cur']
    cur.execute("SELECT estado_resultante FROM pago_evento ORDER BY id_pago_evento DESC LIMIT 1")
    assert cur.fetchone()['estado_resultante'] == 'desconocido'  # solo en la bitacora


def test_pago_aprobado_sobre_venta_cancelada_marca_sin_resucitar(fx):
    cur = fx['cur']
    cur.execute("""UPDATE venta SET id_estado_venta =
                   (SELECT id_estado_venta FROM estado_venta WHERE nombre='cancelado')
                   WHERE id_venta = %s""", (fx['id_venta'],))
    payload, headers = _firmar(fx['id_transaccion'], EstadoPago.CONFIRMADO, Decimal('100.00'))
    r = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    cuerpo = r.get_json()
    assert cuerpo['estado'] == 'venta_cancelada'
    assert _estado(fx, 'pago', 'estado', 'id_pago', fx['id_pago']) == 'pendiente'
    cur.execute("""SELECT ev.nombre AS e FROM venta v JOIN estado_venta ev
                     ON ev.id_estado_venta = v.id_estado_venta WHERE v.id_venta = %s""",
                (fx['id_venta'],))
    assert cur.fetchone()['e'] == 'cancelado'


def test_sin_pago_asociado(fx):
    payload, headers = _firmar('sim_inexistente_xyz', EstadoPago.CONFIRMADO, Decimal('10.00'))
    r = fx['client'].post('/webhooks/pago/simulada', data=payload, headers=headers)
    assert r.status_code == 200
    assert r.get_json()['estado'] == 'sin_pago_asociado'


def test_proveedor_no_configurado_da_404(fx):
    r = fx['client'].post('/webhooks/pago/culqui', data=b'{}',
                          headers={FIRMA_CABECERA: 'x'})
    assert r.status_code == 404
