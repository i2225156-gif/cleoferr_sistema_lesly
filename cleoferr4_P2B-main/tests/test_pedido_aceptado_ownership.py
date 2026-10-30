"""IDOR en /pedido_aceptado: el cliente solo ve SUS pedidos.

Dos clientes sintéticos (AUTH) y sus ventas sintéticas (TIENDA), todo en
transacciones con ROLLBACK: nada persiste. El id de otro cliente responde
EXACTAMENTE igual que un id inexistente (404 y la misma página, sin datos
del otro cliente); el staff conserva el acceso a cualquier venta.

El archivo fija además el comportamiento de las OTRAS rutas del cliente que
buscan por id (mi_comprobante, pago_simulado, pago_simulado_procesar,
mis_pedidos): ninguna entrega datos de otro cliente.
"""
import uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


class ConnProxy:
    """Conexión real con commit/close no-op: la TXN se descarta con rollback."""

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


def _sesion(usuario_id, rol='cliente', csrf='token-test'):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = usuario_id
        s['rol'] = rol
        s['_csrf_token'] = csrf
    return c


@pytest.fixture()
def fx(monkeypatch):
    """Cliente A y cliente B sintéticos, con una venta y un pago cada uno."""
    raw_t = db_supabase.engine_tienda.raw_connection()
    proxy_t = ConnProxy(raw_t)
    cur = proxy_t.cursor(dictionary=True)
    raw_a = db_supabase.engine_auth.raw_connection()
    proxy_a = ConnProxy(raw_a)
    cur_a = proxy_a.cursor(dictionary=True)

    sufijo = uuid.uuid4().hex[:8]
    cur_a.execute("""
        INSERT INTO cliente (nombre, apellido, email, id_tipo_documento,
                             nro_documento, activo, origen)
        VALUES (%s, 'Prueba', %s,
                (SELECT id_tipo_documento FROM tipo_documento WHERE nombre = 'DNI'),
                %s, TRUE, 'pos'),
               (%s, 'Prueba', %s,
                (SELECT id_tipo_documento FROM tipo_documento WHERE nombre = 'DNI'),
                %s, TRUE, 'pos')
        RETURNING id_cliente
    """, (f'idor-cli-a-{sufijo}', f'idor-a-{sufijo}@test.local', sufijo,
          f'idor-cli-b-{sufijo}', f'idor-b-{sufijo}@test.local', sufijo[::-1]))
    cli_a, cli_b = [r['id_cliente'] for r in cur_a.fetchall()]

    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta,
                           num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                %s,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='entregado'),
                %s),
               ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                %s,
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='listo'),
                %s)
        RETURNING id_venta
    """, (cli_a, f'idor-a-{sufijo}', cli_b, f'idor-b-{sufijo}'))
    id_a, id_b = [r['id_venta'] for r in cur.fetchall()]

    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        SELECT x.id_venta,
               (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='tarjeta'),
               NULL, 'pendiente', 100.00, x.trx, 'simulada'
          FROM (VALUES (%s, %s), (%s, %s)) AS x(id_venta, trx)
    """, (id_a, f'idor-trx-a-{sufijo}', id_b, f'idor-trx-b-{sufijo}'))

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy_t)
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy_a)
    yield {'a': _sesion(cli_a), 'b': _sesion(cli_b),
           'staff': _sesion(8, 'vendedor'),
           'cli_a': cli_a, 'cli_b': cli_b, 'id_a': id_a, 'id_b': id_b,
           'trx_a': f'idor-trx-a-{sufijo}', 'trx_b': f'idor-trx-b-{sufijo}',
           'cur': cur}
    proxy_t.rollback()
    raw_t.close()
    proxy_a.rollback()
    raw_a.close()


# ── /pedido_aceptado: el cliente solo ve SUS pedidos ─────────────

def test_cliente_ve_el_estado_de_su_pedido(fx):
    r = fx['a'].get(f'/pedido_aceptado?id={fx["id_a"]}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f'Pedido #{fx["id_a"]}' in html
    assert 'COMPLETADO' in html                      # estado 'entregado'
    assert f'Pedido #{fx["id_b"]}' not in html        # nada del cliente B


def test_pedido_ajeno_es_indistinguible_de_un_pedido_inexistente(fx):
    """El id de otro cliente responde EXACTO como uno que no existe."""
    r_ajeno = fx['a'].get(f'/pedido_aceptado?id={fx["id_b"]}')
    r_inexistente = fx['a'].get(
        f'/pedido_aceptado?id={max(fx["id_a"], fx["id_b"]) + 10000000}')

    assert r_ajeno.status_code == 404
    assert r_ajeno.status_code == r_inexistente.status_code
    assert r_ajeno.data == r_inexistente.data        # misma página, sin pistas
    assert f'Pedido #{fx["id_b"]}'.encode() not in r_ajeno.data
    assert b'LISTO' not in r_ajeno.data              # no revela el estado ajeno


def test_id_que_no_existe_o_no_es_numero_tambien_es_404(fx):
    r_inexistente = fx['a'].get(
        f'/pedido_aceptado?id={max(fx["id_a"], fx["id_b"]) + 10000000}')
    for id_invalido in ('no-es-un-id', '-1', '0', '1e3'):
        r = fx['a'].get(f'/pedido_aceptado?id={id_invalido}')
        assert r.status_code == 404, id_invalido
        assert r.data == r_inexistente.data, id_invalido


def test_sin_id_no_muestra_ningun_pedido(fx):
    """El carrito puede llegar sin id (carrito.html): página genérica, sin datos."""
    r = fx['a'].get('/pedido_aceptado')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f'Pedido #{fx["id_a"]}' not in html
    assert f'Pedido #{fx["id_b"]}' not in html


def test_staff_conserva_el_acceso_a_cualquier_pedido(fx):
    r = fx['staff'].get(f'/pedido_aceptado?id={fx["id_b"]}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f'Pedido #{fx["id_b"]}' in html
    assert '¡LISTO!' in html                          # estado 'listo'


# ── Las demás rutas del cliente que buscan por id ───────────────

def test_mi_comprobante_no_entrega_la_venta_de_otro_cliente(fx):
    r = fx['a'].get(f'/mi_comprobante/{fx["id_b"]}')
    assert r.status_code == 403
    assert f'{fx["id_b"]}'.encode() not in r.data


def test_pago_simulado_no_entrega_el_pago_de_otro_cliente(fx):
    r = fx['a'].get(f'/pago/simulado/{fx["trx_b"]}')
    assert r.status_code == 403


def test_pago_simulado_procesar_no_procesa_el_pago_de_otro_cliente(fx):
    r = fx['a'].post(f'/pago/simulado/{fx["trx_b"]}/procesar',
                     json={'resultado': 'aprobado', 'csrf_token': 'token-test'})
    assert r.status_code == 403
    fx['cur'].execute("SELECT estado FROM pago WHERE id_transaccion = %s",
                      (fx['trx_b'],))
    assert fx['cur'].fetchone()['estado'] == 'pendiente'   # no se tocó


def test_mis_pedidos_no_muestra_los_pedidos_de_otros_clientes(fx):
    r = fx['a'].get('/mis_pedidos')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f'Pedido #{fx["id_a"]}' in html
    assert f'Pedido #{fx["id_b"]}' not in html
