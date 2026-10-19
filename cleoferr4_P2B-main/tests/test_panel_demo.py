"""Tramo 2 (2c): panel de demo de pagos simulados (solo admin, CSRF, bandera).

Rutas reales con test client; conexión proxied con commit() no-op dentro de
una transacción con rollback: nada persiste. El GET del panel nunca escribe.
"""
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
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


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    admin = app.test_client()
    with admin.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Admin'
        s['_csrf_token'] = 'token-test'
    yield {'admin': admin, 'cur': cur, 'proxy': proxy,
           'monkeypatch': monkeypatch}
    proxy.rollback()
    raw.close()


def _pago_pendiente_sintetico(fx, monto=None):
    """venta+pago pendiente con id_transaccion, dentro de la txn del test."""
    cur = fx['cur']
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), NULL,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'), %s)
        RETURNING id_venta
    """, (f"test-{_uuid.uuid4().hex[:12]}",))
    id_venta = cur.fetchone()['id_venta']
    cur.execute("""INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)
                   SELECT %s, id_producto, 1, precio FROM producto ORDER BY id_producto LIMIT 1""",
                (id_venta,))
    cur.execute(f"""SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}),0) AS t
                     FROM detalle_venta WHERE id_venta=%s""", (id_venta,))
    total = cur.fetchone()['t']
    trx = f"sim_test_{_uuid.uuid4().hex[:10]}"
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                NULL, 'pendiente', %s, %s, 'simulada')
        RETURNING id_pago
    """, (id_venta, monto if monto is not None else total, trx))
    id_pago = cur.fetchone()['id_pago']
    return {'id_pago': id_pago, 'id_venta': id_venta, 'trx': trx, 'total': total}


def test_panel_solo_admin(fx):
    r = fx['admin'].get('/admin/pagos_simulados')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'PAGO SIMULADO' in html and 'sin cobro real' in html   # bandera
    for rol, uid in (('vendedor', 8), ('cliente', 11)):
        c = app.test_client()
        with c.session_transaction() as s:
            s['usuario_id'] = uid
            s['rol'] = rol
        assert c.get('/admin/pagos_simulados').status_code == 302
    assert app.test_client().get('/admin/pagos_simulados').status_code == 302


def test_panel_deshabilitado_si_modo_no_simulado(fx):
    fx['monkeypatch'].setattr(modulo_app._PASARELA, 'modo', 'otro')
    assert fx['admin'].get('/admin/pagos_simulados').status_code == 302


def test_panel_get_no_escribe(fx):
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM pago_evento")
    antes = cur.fetchone()['n']
    assert fx['admin'].get('/admin/pagos_simulados').status_code == 200
    cur.execute("SELECT COUNT(*) AS n FROM pago_evento")
    assert cur.fetchone()['n'] == antes


def test_disparar_confirma_pago_pendiente(fx):
    pago = _pago_pendiente_sintetico(fx)
    r = fx['admin'].post('/admin/pagos_simulados/disparar', data={
        'id_pago': pago['id_pago'], 'estado': 'confirmado',
        'csrf_token': 'token-test'})
    assert r.status_code == 302
    cur = fx['cur']
    cur.execute("SELECT estado FROM pago WHERE id_pago=%s", (pago['id_pago'],))
    assert cur.fetchone()['estado'] == 'confirmado'
    cur.execute("""SELECT ev.nombre AS e FROM venta v JOIN estado_venta ev
                     ON ev.id_estado_venta = v.id_estado_venta WHERE v.id_venta=%s""",
                (pago['id_venta'],))
    assert cur.fetchone()['e'] == 'confirmado'
    cur.execute("""SELECT firma_valida, procesado_en FROM pago_evento
                    WHERE id_evento_proveedor LIKE %s ORDER BY id_pago_evento DESC LIMIT 1""",
                (f"evt-{pago['trx']}%",))
    ev = cur.fetchone()
    assert ev['firma_valida'] is True and ev['procesado_en'] is not None


def test_disparar_con_monto_tampered_cae_en_validacion(fx):
    # monto del pago adulterado en BD (999) pero detalle_venta suma otro
    # total: el gatillo recalcula del servidor y confirmar_pago lo rechaza.
    pago = _pago_pendiente_sintetico(fx, monto=999)
    r = fx['admin'].post('/admin/pagos_simulados/disparar', data={
        'id_pago': pago['id_pago'], 'estado': 'confirmado',
        'csrf_token': 'token-test'})
    assert r.status_code == 302
    cur = fx['cur']
    cur.execute("SELECT estado FROM pago WHERE id_pago=%s", (pago['id_pago'],))
    assert cur.fetchone()['estado'] == 'pendiente'
    cur.execute("""SELECT ev.nombre AS e FROM venta v JOIN estado_venta ev
                     ON ev.id_estado_venta = v.id_estado_venta WHERE v.id_venta=%s""",
                (pago['id_venta'],))
    assert cur.fetchone()['e'] == 'pendiente'


def test_disparar_sin_csrf_es_400(fx):
    pago = _pago_pendiente_sintetico(fx)
    r = fx['admin'].post('/admin/pagos_simulados/disparar', data={
        'id_pago': pago['id_pago'], 'estado': 'confirmado'})
    assert r.status_code == 400


def test_disparar_deshabilitado_si_modo_no_simulado(fx):
    fx['monkeypatch'].setattr(modulo_app._PASARELA, 'modo', 'otro')
    pago = _pago_pendiente_sintetico(fx)
    r = fx['admin'].post('/admin/pagos_simulados/disparar', data={
        'id_pago': pago['id_pago'], 'estado': 'confirmado',
        'csrf_token': 'token-test'})
    assert r.status_code == 403


def test_panel_muestra_recibido_en_en_hora_lima(fx):
    """La celda 'Recibido' muestra la pared Lima, no la UTC (+5h).

    Evento sintético con recibido_en fijado a las 23:00 Lima del 18/10
    (= 04:00 UTC del 19/10): la fila del panel renderiza '18/10 23:00:00'
    junto al marcador del evento; el display legado solo habría mostrado
    '19/10 04:00:00' si la sesión no estuviera en America/Lima: con la
    sesión en Lima se ve igual, y el cambio es un seguro contra una
    reconfiguración del servidor.
    """
    import re

    cur = fx['cur']
    marcador = f"evt-tzfix-{_uuid.uuid4().hex[:10]}"
    cur.execute("""
        INSERT INTO pago_evento (proveedor, id_evento_proveedor, tipo,
                                  estado_resultante, firma_valida,
                                  dentro_de_tolerancia, recibido_en)
        VALUES ('simulada', %s, 'pago_actualizado', 'confirmado', TRUE, TRUE,
                '2026-10-18 23:00:00-05'::timestamptz)
    """, (marcador,))
    html = fx['admin'].get('/admin/pagos_simulados').get_data(as_text=True)
    celda = re.compile(
        r"(\d{2}/\d{2} \d{2}:\d{2}:\d{2})</td>\s*"
        r'<td style="font-size:0\.75rem"><code>' + re.escape(marcador[:28]))
    m = celda.search(html)
    assert m, 'la fila del evento sintético no apareció en el panel'
    assert m.group(1) == '18/10 23:00:00'
