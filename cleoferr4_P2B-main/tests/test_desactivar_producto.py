"""TAREA 3.4 — POST /productos/desactivar/<id> con CSRF (RF4 «obsoleto»).

La baja es reversible (desde Editar) y conserva el historial; la
eliminación física sigue bloqueada por historial. Solo POST: GET → 405,
sin token → 400. Con rollback en todos los caminos que escriben.
"""
import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


class ConnProxy:
    """Contrato de conexión proxied con rollback (como P1..P4)."""

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


def _sesion(rol='vendedor', uid=8):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock)
        VALUES ('zzqdes-prod', 5.00, 3)
        RETURNING id_producto
    """)
    pid = cur.fetchone()['id_producto']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'pid': pid}
    proxy.rollback()
    raw.close()


def _activo(fx):
    fx['cur'].execute("SELECT activo FROM producto WHERE id_producto = %s",
                      (fx['pid'],))
    return fx['cur'].fetchone()['activo']


def test_desactiva_con_token(fx):
    r = _sesion().post(f"/productos/desactivar/{fx['pid']}",
                       data={'csrf_token': 'token-test'},
                       follow_redirects=True)
    assert r.status_code == 200
    assert 'Producto desactivado' in r.get_data(as_text=True)
    assert _activo(fx) is False


def test_sin_token_es_400(fx):
    r = _sesion().post(f"/productos/desactivar/{fx['pid']}", data={})
    assert r.status_code == 400
    assert _activo(fx) is True


def test_get_es_405(fx):
    r = _sesion().get(f"/productos/desactivar/{fx['pid']}")
    assert r.status_code == 405
    assert _activo(fx) is True


def test_sin_sesion_es_400_por_csrf(fx):
    """El CSRF corre ANTES que la sesión (fail closed): un POST anónimo
    sin token válido da 400, no el redirect del login."""
    r = app.test_client().post(f"/productos/desactivar/{fx['pid']}",
                               data={'csrf_token': 'x'})
    assert r.status_code == 400
    assert _activo(fx) is True


def test_cliente_bloqueado(fx):
    r = _sesion(rol='cliente', uid=11).post(f"/productos/desactivar/{fx['pid']}",
                                             data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    assert _activo(fx) is True


def test_inexistente_es_404(fx):
    r = _sesion().post('/productos/desactivar/999999',
                       data={'csrf_token': 'token-test'})
    assert r.status_code == 404


def test_ya_inactivo_avisa_sin_tocar(fx):
    c = _sesion()
    c.post(f"/productos/desactivar/{fx['pid']}",
           data={'csrf_token': 'token-test'}, follow_redirects=True)
    r = c.post(f"/productos/desactivar/{fx['pid']}",
               data={'csrf_token': 'token-test'}, follow_redirects=True)
    assert 'ya estaba inactivo' in r.get_data(as_text=True)
    assert _activo(fx) is False


def test_boton_presente_para_activos_y_ausente_para_inactivos(fx):
    fx['cur'].execute("""
        INSERT INTO producto (nombre, precio, stock, activo)
        VALUES ('zzqdes-inactivo', 5.00, 3, FALSE)
        RETURNING id_producto
    """)
    pid_inactivo = fx['cur'].fetchone()['id_producto']
    html = _sesion().get('/productos?pagina=99999').get_data(as_text=True)
    assert f'action="/productos/desactivar/{fx["pid"]}"' in html
    assert f'action="/productos/desactivar/{pid_inactivo}"' not in html
    assert '¿Desactivar este producto?' in html
