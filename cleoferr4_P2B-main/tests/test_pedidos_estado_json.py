"""Fix del bug del desplegable de /pedidos (contrato JSON, CSRF y transiciones).

Sin token el CSRF devolvía HTML 400 y el fetch hacía .json() sobre
<!doctype> ("Unexpected token '<'"). Contrato nuevo: si la petición es
AJAX, TODO es JSON {ok, mensaje, estado_nuevo?}.

Los tests que escriben usan ConnProxy con rollback (nada persiste); el
pedido 223 del propietario NO se toca (las transiciones inválidas y los
no-op no escriben; las válidas usan ventas sintéticas del test).
"""
import re
import uuid as _uuid
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app

AJAX = {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}
PROYECTO = Path(__file__).resolve().parent.parent


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


def _sesion(**campos):
    c = app.test_client()
    with c.session_transaction() as s:
        s['_csrf_token'] = 'token-test'
        s.update(campos)
    return c


def _post(c, id_venta, estado, token='token-test'):
    datos = {'estado': estado}
    if token is not None:
        datos['csrf_token'] = token
    return c.post(f'/pedidos/{id_venta}/estado', data=datos, headers=AJAX)


def _es_json(r):
    return 'application/json' in (r.headers.get('Content-Type') or '')


# ── Contrato JSON en los errores de las capas globales ──────────

def test_ajax_sin_token_devuelve_json_400():
    r = _post(_sesion(usuario_id=8, rol='vendedor'), 1, 'preparando', token=None)
    assert r.status_code == 400 and _es_json(r)
    cuerpo = r.get_json()
    assert cuerpo['ok'] is False and 'CSRF' in cuerpo['mensaje']


def test_ajax_token_invalido_devuelve_json_400():
    r = _post(_sesion(usuario_id=8, rol='vendedor'), 1, 'preparando', token='otro')
    assert r.status_code == 400 and _es_json(r)
    assert r.get_json()['ok'] is False


def test_ajax_sesion_expirada_devuelve_json_401():
    # sesión con token válido pero sin usuario_id (simula sesión vencida)
    r = _post(_sesion(), 1, 'preparando')
    assert r.status_code == 401 and _es_json(r)
    assert 'sesión expiró' in r.get_json()['mensaje']


def test_ajax_rol_sin_permisos_devuelve_json_403():
    r = _post(_sesion(usuario_id=11, rol='cliente'), 1, 'preparando')
    assert r.status_code == 403 and _es_json(r)
    assert r.get_json()['ok'] is False


def test_ajax_error_de_bd_devuelve_json_500(monkeypatch):
    class CursorRoto:
        def execute(self, *a, **k):
            raise RuntimeError('boom')

    class ConnRota:
        def cursor(self, *a, **k):
            return CursorRoto()

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: ConnRota())
    r = _post(_sesion(usuario_id=8, rol='vendedor'), 1, 'preparando')
    assert r.status_code == 500 and _es_json(r)
    assert r.get_json()['ok'] is False


# ── Transiciones y escrituras (rollback, nada persiste) ─────────

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
        " VALUES (%s, %s, 1, 10.00)", (id_venta, id_producto))
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'client': _sesion(usuario_id=8, rol='vendedor'), 'cur': cur,
           'id_venta': id_venta, 'id_producto': id_producto}
    proxy.rollback()
    raw.close()


def test_ajax_transicion_invalida_pendiente_a_preparando_409(fx):
    r = _post(fx['client'], fx['id_venta'], 'preparando')
    assert r.status_code == 409 and _es_json(r)
    assert r.get_json()['mensaje'] == 'Confirma el pago antes de preparar el pedido.'
    cur = fx['cur']
    cur.execute("""SELECT ev.nombre AS e FROM venta v JOIN estado_venta ev
                     ON ev.id_estado_venta = v.id_estado_venta
                    WHERE v.id_venta = %s""", (fx['id_venta'],))
    assert cur.fetchone()['e'] == 'pendiente'   # no escribió nada


def test_ajax_estado_invalido_400(fx):
    r = _post(fx['client'], fx['id_venta'], 'inexistente')
    assert r.status_code == 400 and _es_json(r)
    assert r.get_json()['ok'] is False


def test_ajax_pedido_inexistente_404(fx):
    r = _post(fx['client'], 99999999, 'preparando')
    assert r.status_code == 404 and _es_json(r)


def test_ajax_mismo_estado_es_noop(fx):
    r = _post(fx['client'], fx['id_venta'], 'pendiente')
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['ok'] is True and cuerpo['estado_nuevo'] == 'pendiente'


def _insertar_pago(fx, estado_pago):
    """Pago sintético para la venta del fixture (misma transacción)."""
    fx['cur'].execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto, id_transaccion)
        VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                NULL, %s, 10.00, 'sim_test_bloqueo')
    """, (fx['id_venta'], estado_pago))


def test_ajax_confirmar_con_pago_pendiente_409(fx):
    _insertar_pago(fx, 'pendiente')
    r = _post(fx['client'], fx['id_venta'], 'confirmado')
    assert r.status_code == 409 and _es_json(r)
    assert r.get_json()['mensaje'] == ('El pago no está confirmado; '
                                        'confírmalo desde el flujo de pago.')
    cur = fx['cur']
    cur.execute("""SELECT ev.nombre AS e FROM venta v JOIN estado_venta ev
                     ON ev.id_estado_venta = v.id_estado_venta
                    WHERE v.id_venta = %s""", (fx['id_venta'],))
    assert cur.fetchone()['e'] == 'pendiente'   # no escribió nada


def test_ajax_confirmar_sin_pago_409(fx):
    r = _post(fx['client'], fx['id_venta'], 'confirmado')
    assert r.status_code == 409 and _es_json(r)
    assert 'El pago no está confirmado' in r.get_json()['mensaje']


def test_ajax_confirmar_con_pago_confirmado_200(fx):
    _insertar_pago(fx, 'confirmado')
    r = _post(fx['client'], fx['id_venta'], 'confirmado')
    assert r.status_code == 200
    assert r.get_json()['estado_nuevo'] == 'confirmado'


def test_ajax_transicion_valida_a_confirmado_audita(fx):
    _insertar_pago(fx, 'confirmado')   # bloqueo (a): exige pago confirmado
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM auditoria")
    antes = cur.fetchone()['n']
    r = _post(fx['client'], fx['id_venta'], 'confirmado')
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['ok'] is True and cuerpo['estado_nuevo'] == 'confirmado'
    cur.execute("SELECT COUNT(*) AS n FROM auditoria")
    assert cur.fetchone()['n'] == antes + 1   # el trigger auditó el cambio


def test_ajax_cancelado_devuelve_stock_y_es_idempotente(fx):
    marca = f"Devolución stock venta #{fx['id_venta']} (producto #{fx['id_producto']})"
    r = _post(fx['client'], fx['id_venta'], 'cancelado')
    assert r.status_code == 200 and r.get_json()['estado_nuevo'] == 'cancelado'
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE observacion = %s",
                (marca,))
    assert cur.fetchone()['n'] == 1            # B2 devolvió el stock
    r2 = _post(fx['client'], fx['id_venta'], 'cancelado')
    assert r2.status_code == 200
    assert 'ya estaba' in r2.get_json()['mensaje']
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE observacion = %s",
                (marca,))
    assert cur.fetchone()['n'] == 1            # no duplicó la devolución


def test_ajax_entregado_es_final(fx):
    _insertar_pago(fx, 'confirmado')   # para poder pasar por 'confirmado'
    cur = fx['cur']
    for estado in ('confirmado', 'preparando', 'listo', 'entregado'):
        r = _post(fx['client'], fx['id_venta'], estado)
        assert r.status_code == 200, f"falló la transición a {estado}"
    r = _post(fx['client'], fx['id_venta'], 'preparando')   # desde entregado
    assert r.status_code == 409 and _es_json(r)


# ── Frontend: token en el JS y desplegable filtrado ─────────────

def test_plantillas_incluyen_token_y_validacion_de_json():
    pedidos_html = (PROYECTO / 'templates' / 'pedidos.html').read_text(encoding='utf-8')
    assert 'X-CSRFToken' in pedidos_html                    # token en cabecera
    assert 'csrf_token|tojson' in pedidos_html               # fuente del token
    assert 'Content-Type' in pedidos_html                    # validar antes de .json()
    assert 'sesion-expirada' in pedidos_html                 # manejo de 401
    detalle_html = (PROYECTO / 'templates' / 'pedido_detalle.html').read_text(encoding='utf-8')
    assert 'name="csrf_token"' in detalle_html               # form clásico con token


def test_menu_filtra_opciones_por_estado_actual():
    r = _sesion(usuario_id=8, rol='vendedor').get('/pedidos')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    bloque = re.search(r'data-estado-actual="pendiente".*?</select>', html, re.S)
    assert bloque, 'no se encontró un pedido pendiente en la lista'
    opciones = bloque.group(0)
    assert 'value="confirmado"' in opciones      # permitido
    assert 'value="cancelado"' in opciones      # permitido
    assert 'value="preparando"' not in opciones  # filtrado (requiere pago confirmado)
    assert 'value="entregado"' not in opciones   # filtrado
