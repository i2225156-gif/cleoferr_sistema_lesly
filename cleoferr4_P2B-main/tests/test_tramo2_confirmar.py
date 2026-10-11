"""Tramo 2 (2b): confirmar_pago — única puerta a 'confirmado'.

Contra la BD real pero DENTRO de una transacción con rollback: nada
persiste. La conexión se inyecta para que confirmar_pago no commitee.
La concurrencia se prueba bloqueando con FOR UPDATE un pago REAL
pendiente (legacy, sin monto/id_transaccion): 'bloqueado' no debe tener
efectos y el desbloqueo cae en la validación, jamás en una confirmación.
"""
import uuid as _uuid
from decimal import Decimal

import pytest

import db_supabase
from db_supabase import _CompatConnection
from services.pagos import confirmar_pago
from services.pasarela.base import EstadoPago, PagoResultado


@pytest.fixture()
def fx():
    raw = db_supabase.engine_tienda.raw_connection()
    conn = _CompatConnection(raw)
    cur = conn.cursor(dictionary=True)
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
    datos = {'conn': conn, 'cur': cur, 'id_venta': id_venta,
             'id_pago': fila['id_pago'], 'id_transaccion': fila['id_transaccion']}
    yield datos
    conn.rollback()
    conn.close()


def _resultado(trx, estado=EstadoPago.CONFIRMADO, monto=Decimal('100.00')):
    return PagoResultado(id_pago_proveedor=trx, estado=estado, monto=monto)


def _estado_pago(cur, id_pago):
    cur.execute("SELECT estado FROM pago WHERE id_pago = %s", (id_pago,))
    return cur.fetchone()['estado']


def _estado_venta(cur, id_venta):
    cur.execute("""SELECT ev.nombre AS estado FROM venta v
                     JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
                    WHERE v.id_venta = %s""", (id_venta,))
    return cur.fetchone()['estado']


def _auditoria(cur, cond="TRUE"):
    cur.execute(f"SELECT COUNT(*) AS n FROM auditoria WHERE {cond}")
    return cur.fetchone()['n']


def test_confirmacion_idempotente(fx):
    conn, cur = fx['conn'], fx['cur']
    aud_antes = _auditoria(cur)
    r1 = confirmar_pago(fx['id_pago'], _resultado(fx['id_transaccion']), conn=conn)
    assert r1['estado'] == 'confirmado'
    assert _estado_pago(cur, fx['id_pago']) == 'confirmado'
    assert _estado_venta(cur, fx['id_venta']) == 'confirmado'
    r2 = confirmar_pago(fx['id_pago'], _resultado(fx['id_transaccion']), conn=conn)
    assert r2['estado'] == 'ya_confirmado'
    assert _estado_pago(cur, fx['id_pago']) == 'confirmado'
    # El trigger audito UNA sola vez: el reintento no duplica efectos.
    assert _auditoria(cur) == aud_antes + 1


def test_monto_distinto_no_confirma_y_registra(fx):
    conn, cur = fx['conn'], fx['cur']
    aud_antes = _auditoria(cur)
    r = confirmar_pago(fx['id_pago'], _resultado(fx['id_transaccion'],
                                                  monto=Decimal('999.00')), conn=conn)
    assert r['estado'] == 'invalido'
    assert 'monto' in r['motivo']
    assert _estado_pago(cur, fx['id_pago']) == 'pendiente'
    assert _estado_venta(cur, fx['id_venta']) == 'pendiente'
    assert _auditoria(cur, "accion = 'validacion_rechazada'") == aud_antes - aud_antes + 1


def test_transaccion_distinta_no_confirma(fx):
    r = confirmar_pago(fx['id_pago'], _resultado('sim_otra_cosa'), conn=fx['conn'])
    assert r['estado'] == 'invalido'
    assert 'transaccion' in r['motivo'] or 'id de pago' in r['motivo']
    assert _estado_pago(fx['cur'], fx['id_pago']) == 'pendiente'


def test_pago_aprobado_sobre_venta_cancelada_no_resucita(fx):
    conn, cur = fx['conn'], fx['cur']
    cur.execute("""UPDATE venta SET id_estado_venta =
                   (SELECT id_estado_venta FROM estado_venta WHERE nombre='cancelado')
                   WHERE id_venta = %s""", (fx['id_venta'],))
    r = confirmar_pago(fx['id_pago'], _resultado(fx['id_transaccion']), conn=conn)
    assert r['estado'] == 'venta_cancelada'
    assert _estado_pago(cur, fx['id_pago']) == 'pendiente'
    assert _estado_venta(cur, fx['id_venta']) == 'cancelado'
    assert _auditoria(cur, "accion = 'pago_sobre_venta_cancelada'") == 1


def test_rechazado_ya_rechazado(fx):
    conn, cur = fx['conn'], fx['cur']
    r1 = confirmar_pago(fx['id_pago'], _resultado(fx['id_transaccion'],
                                                  estado=EstadoPago.RECHAZADO), conn=conn)
    assert r1['estado'] == 'rechazado'
    assert _estado_pago(cur, fx['id_pago']) == 'rechazado'
    assert _estado_venta(cur, fx['id_venta']) == 'pendiente'
    r2 = confirmar_pago(fx['id_pago'], _resultado(fx['id_transaccion'],
                                                  estado=EstadoPago.RECHAZADO), conn=conn)
    assert r2['estado'] == 'ya_rechazado'


def test_desconocido_nunca_llega_a_pago_estado(fx):
    conn, cur = fx['conn'], fx['cur']
    r = confirmar_pago(fx['id_pago'], _resultado(fx['id_transaccion'],
                                                 estado=EstadoPago.DESCONOCIDO), conn=conn)
    assert r['estado'] == 'ignorado'
    assert _estado_pago(cur, fx['id_pago']) == 'pendiente'


def test_concurrencia_doble_bloquea_sin_efectos(monkeypatch):
    monkeypatch.setenv('PAGO_LOCK_TIMEOUT', '1s')
    raw = db_supabase.engine_tienda.raw_connection()
    conn = _CompatConnection(raw)
    cur = conn.cursor(dictionary=True)
    raw_bloqueo = db_supabase.engine_tienda.raw_connection()
    conn_bloqueo = _CompatConnection(raw_bloqueo)
    try:
        # Un pago REAL pendiente (legacy: sin monto ni id_transaccion).
        cur.execute("SELECT id_pago FROM pago WHERE estado = 'pendiente' ORDER BY id_pago LIMIT 1")
        id_real = cur.fetchone()['id_pago']
        bloqueo = conn_bloqueo.cursor(dictionary=True)
        bloqueo.execute("SELECT 1 FROM pago WHERE id_pago = %s FOR UPDATE", (id_real,))
        r = confirmar_pago(id_real, _resultado('sim_cualquiera', monto=Decimal('1.00')), conn=conn)
        assert r['estado'] == 'bloqueado'
        conn_bloqueo.rollback()  # libera el lock
        r2 = confirmar_pago(id_real, _resultado('sim_cualquiera', monto=Decimal('1.00')), conn=conn)
        # El pago legacy no cumple las validaciones: NUNCA se confirma.
        assert r2['estado'] == 'invalido'
        cur.execute("SELECT estado FROM pago WHERE id_pago = %s", (id_real,))
        assert cur.fetchone()['estado'] == 'pendiente'
    finally:
        conn_bloqueo.rollback()
        conn_bloqueo.close()
        conn.rollback()
        conn.close()
