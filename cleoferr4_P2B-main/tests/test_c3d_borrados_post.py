"""C3d: borrados de producto y cliente SOLO por POST con CSRF.

- GET a las rutas → 405 (antes eran GET sin protección).
- POST sin token (o con token inválido) → 400 (_validar_csrf).
- POST con token: borrado real de un CLIENTE sintético dentro de una
  transacción con rollback sobre la BD auth (la ruta usa
  get_connection_auth: el commit del proxy no persiste nada).
- Las plantillas renderizan <form method="POST"> con data-confirm tipo
  peligro y token CSRF; ya no queda data-href (el interceptor de enlaces
  del componente sigue disponible pero sin uso).
"""
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


def _admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


def test_get_a_borrados_es_405():
    c = _admin()
    assert c.get('/productos/eliminar/1').status_code == 405
    assert c.get('/clientes/eliminar/1').status_code == 405


def test_post_sin_csrf_es_400():
    c = _admin()
    assert c.post('/productos/eliminar/1').status_code == 400
    assert c.post('/clientes/eliminar/1').status_code == 400
    # Token inválido: mismo rechazo (comparación hmac del global)
    assert c.post('/productos/eliminar/1',
                  data={'csrf_token': 'otro'}).status_code == 400


class _ProxyAuth:
    """Contrato de conexión proxied con rollback (como test_admin_layout)."""

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
def fx_auth(monkeypatch):
    raw = db_supabase.engine_auth.raw_connection()
    proxy = _ProxyAuth(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(
        "INSERT INTO cliente (nombre, apellido, id_tipo_documento,"
        " nro_documento, activo, origen)"
        " VALUES (%s, %s, 1, %s, TRUE, 'pos')"
        " RETURNING id_cliente",
        (f"test-c3d-{_uuid.uuid4().hex[:10]}", 'prueba',
         f"{_uuid.uuid4().int % 100000000:08d}"))
    cid = cur.fetchone()['id_cliente']
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy)
    yield {'admin': _admin(), 'id_cliente': cid, 'cur': cur}
    proxy.rollback()
    raw.close()


def test_cliente_sintetico_eliminado_con_csrf_rollback(fx_auth):
    cid = fx_auth['id_cliente']
    r = fx_auth['admin'].post(f'/clientes/eliminar/{cid}',
                              data={'csrf_token': 'token-test'},
                              follow_redirects=True)
    assert r.status_code == 200
    assert 'Cliente eliminado' in r.get_data(as_text=True)
    # Borrado DENTRO de la transacción (el rollback del fixture lo deshace)
    fx_auth['cur'].execute(
        "SELECT COUNT(*) AS n FROM cliente WHERE id_cliente = %s", (cid,))
    assert fx_auth['cur'].fetchone()['n'] == 0


def test_plantillas_render_form_post_sin_data_href():
    c = _admin()
    html = c.get('/productos').get_data(as_text=True)
    assert '<form method="POST" action="/productos/eliminar/' in html
    assert 'data-confirm="¿Eliminar este producto?"' in html
    assert '<input type="hidden" name="csrf_token"' in html
    html = c.get('/clientes').get_data(as_text=True)
    assert '<form method="POST" action="/clientes/eliminar/' in html
    assert 'data-confirm="¿Eliminar este cliente?"' in html
    assert '<input type="hidden" name="csrf_token"' in html
