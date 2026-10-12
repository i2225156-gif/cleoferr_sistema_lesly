"""Fase C: reservas de stock vencidas.

Contra la BD real dentro de transacciones con rollback: nada persiste.
Cubre: vence/no vence por tipo de pago, rechazado intacto, confirmada
intacta, POS intacto, doble ejecución (idempotente), GET sin escritura,
sin CSRF 400, no-admin bloqueado.
"""
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.reservas import liberar_reservas_vencidas, preview_reservas_vencidas


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


def _sesion(rol='administrador', uid=7):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['_csrf_token'] = 'token-test'
    return c


def _venta_pendiente(cur, tipo_pago='tarjeta', fecha_offset_seg=None,
                      pago_estado='pendiente', tipo_venta='online'):
    """Crea una venta con pago al estado indicado, con fecha opcionalmente desplazada al pasado."""
    offset = ""
    params = []
    if fecha_offset_seg is not None:
        offset = " NOW() - make_interval(secs => %s) "
        params.append(fecha_offset_seg)
    else:
        offset = " NOW() "
    cur.execute(f"""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion, fecha)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre=%s), 11,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
                %s, {offset})
        RETURNING id_venta
    """, (tipo_venta, f"test-{_uuid.uuid4().hex[:10]}", *params))
    id_venta = cur.fetchone()['id_venta']
    cur.execute(
        "INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)"
        " VALUES (%s, (SELECT id_producto FROM producto ORDER BY id_producto LIMIT 1), 1, 10.00)",
        (id_venta,))
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre=%s),
                NULL, %s, 10.00, %s, 'simulada')
    """, (id_venta, tipo_pago, pago_estado, f"sim_{_uuid.uuid4().hex[:8]}"))
    return id_venta


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'admin': _sesion(), 'monkeypatch': monkeypatch}
    proxy.rollback()
    raw.close()


# ── Preview y liberación (lógica de negocio) ─────────────────────

def test_preview_encuentra_solo_vencidas(fx):
    cur = fx['cur']
    # vencida: tarjeta, hace 31 min
    id_vencida = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=31 * 60)
    # no vencida: tarjeta, hace 5 min
    id_reciente = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=5 * 60)
    # vencida: efectivo, hace 25 h
    id_vencida_op = _venta_pendiente(cur, 'efectivo', fecha_offset_seg=25 * 3600)
    # NO vencida: efectivo, hace 10 h (plazo 24 h)
    id_reciente_op = _venta_pendiente(cur, 'efectivo', fecha_offset_seg=10 * 3600)

    preview = preview_reservas_vencidas(cur)
    ids = {v['id_venta'] for v in preview}
    assert id_vencida in ids
    assert id_vencida_op in ids
    assert id_reciente not in ids
    assert id_reciente_op not in ids


def test_liberar_cancela_venta_pago_y_devuelve_stock(fx):
    cur = fx['cur']
    id_venta = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=35 * 60)
    cur.execute("SELECT id_producto FROM detalle_venta WHERE id_venta=%s", (id_venta,))
    id_producto = cur.fetchone()['id_producto']
    cur.execute("SELECT stock FROM producto WHERE id_producto=%s", (id_producto,))
    stock_antes = cur.fetchone()['stock']

    resultado = liberar_reservas_vencidas(fx['proxy'])
    assert id_venta in resultado['ids']
    assert resultado['liberadas'] >= 1

    # venta y pago cancelados
    cur.execute("""SELECT ev.nombre AS e FROM venta v
        JOIN estado_venta ev ON ev.id_estado_venta=v.id_estado_venta
        WHERE v.id_venta=%s""", (id_venta,))
    assert cur.fetchone()['e'] == 'cancelado'
    cur.execute("SELECT estado FROM pago WHERE id_venta=%s", (id_venta,))
    assert cur.fetchone()['estado'] == 'cancelado'
    # stock devuelto (B2)
    cur.execute("SELECT stock FROM producto WHERE id_producto=%s", (id_producto,))
    assert cur.fetchone()['stock'] == stock_antes + 1
    # movimiento B2 con marca
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento"
                " WHERE observacion = %s",
                (f"Devolución stock venta #{id_venta} (producto #{id_producto})",))
    assert cur.fetchone()['n'] == 1


def test_liberar_no_toca_rechazado(fx):
    cur = fx['cur']
    id_rechazado = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=40 * 60,
                                     pago_estado='rechazado')
    resultado = liberar_reservas_vencidas(fx['proxy'])
    assert id_rechazado not in resultado['ids']
    cur.execute("""SELECT ev.nombre FROM venta v
        JOIN estado_venta ev ON ev.id_estado_venta=v.id_estado_venta
        WHERE v.id_venta=%s""", (id_rechazado,))
    assert cur.fetchone()['nombre'] == 'pendiente'
    cur.execute("SELECT estado FROM pago WHERE id_venta=%s", (id_rechazado,))
    assert cur.fetchone()['estado'] == 'rechazado'


def test_liberar_no_toca_confirmada(fx):
    cur = fx['cur']
    id_confirmada = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=40 * 60,
                                      pago_estado='confirmado')
    resultado = liberar_reservas_vencidas(fx['proxy'])
    assert id_confirmada not in resultado['ids']
    cur.execute("SELECT estado FROM pago WHERE id_venta=%s", (id_confirmada,))
    assert cur.fetchone()['estado'] == 'confirmado'


def test_liberar_no_toca_pos(fx):
    cur = fx['cur']
    id_pos = _venta_pendiente(cur, 'efectivo', fecha_offset_seg=25 * 3600,
                                tipo_venta='local')
    resultado = liberar_reservas_vencidas(fx['proxy'])
    assert id_pos not in resultado['ids']


def test_liberar_es_idempotente(fx):
    cur = fx['cur']
    id_venta = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=31 * 60)
    cur.execute("SELECT id_producto FROM detalle_venta WHERE id_venta=%s", (id_venta,))
    id_producto = cur.fetchone()['id_producto']
    cur.execute("SELECT stock FROM producto WHERE id_producto=%s", (id_producto,))
    stock_antes = cur.fetchone()['stock']

    r1 = liberar_reservas_vencidas(fx['proxy'])
    r2 = liberar_reservas_vencidas(fx['proxy'])
    assert id_venta in r1['ids']
    assert id_venta not in r2['ids']      # ya no aparece en la 2a
    cur.execute("SELECT stock FROM producto WHERE id_producto=%s", (id_producto,))
    assert cur.fetchone()['stock'] == stock_antes + 1   # solo +1
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento"
                " WHERE observacion = %s",
                (f"Devolución stock venta #{id_venta} (producto #{id_producto})",))
    assert cur.fetchone()['n'] == 1       # solo 1 movimiento B2


# ── Rutas HTTP ───────────────────────────────────────────────────

def test_get_panel_solo_lectura(fx):
    cur = fx['cur']
    id_venta = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=31 * 60)
    cur.execute("SELECT COUNT(*) AS n FROM venta WHERE id_estado_venta ="
                " (SELECT id_estado_venta FROM estado_venta WHERE nombre='cancelado')")
    canceladas_antes = cur.fetchone()['n']
    r = fx['admin'].get('/admin/reservas')
    assert r.status_code == 200
    # el GET no canceló nada
    cur.execute("SELECT COUNT(*) AS n FROM venta WHERE id_estado_venta ="
                " (SELECT id_estado_venta FROM estado_venta WHERE nombre='cancelado')")
    assert cur.fetchone()['n'] == canceladas_antes


def test_post_liberar_con_csrf(fx):
    cur = fx['cur']
    id_venta = _venta_pendiente(cur, 'tarjeta', fecha_offset_seg=31 * 60)
    r = fx['admin'].post('/admin/reservas/liberar',
                          data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    cur.execute("""SELECT ev.nombre FROM venta v
        JOIN estado_venta ev ON ev.id_estado_venta=v.id_estado_venta
        WHERE v.id_venta=%s""", (id_venta,))
    assert cur.fetchone()['nombre'] == 'cancelado'


def test_post_sin_csrf_400(fx):
    r = fx['admin'].post('/admin/reservas/liberar', data={})
    assert r.status_code == 400


def test_post_vendedor_bloqueado(fx):
    c = _sesion('vendedor', 8)
    r = c.post('/admin/reservas/liberar', data={'csrf_token': 'token-test'})
    assert r.status_code == 302   # redirect a productos


def test_get_vendedor_bloqueado(fx):
    c = _sesion('vendedor', 8)
    r = c.get('/admin/reservas')
    assert r.status_code == 302
