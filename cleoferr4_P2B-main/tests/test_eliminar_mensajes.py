"""Eliminación de CLIENTE y PROVEEDOR: mensajes de error (sin BD).

Las dos rutas de borrado destructivo NO deben exponer el detalle
técnico de la BD (antes flash(f"Error: {e}")). Ahora:
- violación de llave foránea → aviso amigable con la sugerencia de
  editar en lugar de borrar;
- cualquier otro error → app.logger.exception(...) + aviso genérico.

/proveedores/eliminar ya es SOLO POST con CSRF (como los demás
borrados de C3d). Conexiones falsas vía monkeypatch: sin BD, sin
Supabase y sin fixtures de rollback. El flash se lee directamente de
la sesión (sin seguir el redirect: el listado tocado también quedaría
conectado a la conexión falsa).
"""
from psycopg2 import errors as _pg_errores

from app import app


MENSAJE_FK = ('No se puede eliminar: tiene registros asociados '
              '(productos, ventas o movimientos). '
              'Puedes editarlo en su lugar.')
MENSAJE_GENERICO = 'No se pudo eliminar. Inténtalo de nuevo.'


class _CursorFalso:
    def __init__(self, excepcion):
        self._excepcion = excepcion

    def execute(self, sql, params=None):
        if self._excepcion is not None:
            raise self._excepcion


class _ConnFalsa:
    def __init__(self, excepcion):
        self._excepcion = excepcion
        self.hubo_commit = False
        self.hubo_rollback = False

    def cursor(self, dictionary=False, **_):
        return _CursorFalso(self._excepcion)

    def commit(self):
        self.hubo_commit = True

    def rollback(self):
        self.hubo_rollback = True

    def close(self):
        pass


def _admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['_csrf_token'] = 'token-test'
    return c


def _flashes(cliente):
    with cliente.session_transaction() as s:
        return list(s.get('_flashes', []))


def _conexiones(monkeypatch, getter, excepcion):
    conn = _ConnFalsa(excepcion)
    monkeypatch.setattr('app.get_connection_auth',
                        lambda: conn, raising=False)
    monkeypatch.setattr('app.get_connection_tienda',
                        lambda: conn, raising=False)
    return conn


# ── cliente: mensajes amigables ──────────────────────────────────

def test_cliente_fk_mensaje_amigable(monkeypatch):
    cliente = _admin()
    conn = _conexiones(monkeypatch, 'get_connection_auth',
                       _pg_errores.ForeignKeyViolation())
    r = cliente.post('/clientes/eliminar/5',
                     data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    assert ('danger', MENSAJE_FK) in _flashes(cliente)
    assert conn.hubo_rollback and not conn.hubo_commit


def test_cliente_otro_error_mensaje_generico(monkeypatch):
    cliente = _admin()
    conn = _conexiones(monkeypatch, 'get_connection_auth',
                       ValueError('detalle tecnico secreto'))
    r = cliente.post('/clientes/eliminar/5',
                     data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    flashes = _flashes(cliente)
    assert ('danger', MENSAJE_GENERICO) in flashes
    # Nunca el detalle técnico de la excepción hacia el usuario
    assert not any('detalle tecnico' in msg for _, msg in flashes)
    assert conn.hubo_rollback and not conn.hubo_commit


def test_cliente_camino_feliz(monkeypatch):
    cliente = _admin()
    conn = _conexiones(monkeypatch, 'get_connection_auth', None)
    r = cliente.post('/clientes/eliminar/5',
                     data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    assert ('success', 'Cliente eliminado.') in _flashes(cliente)
    assert conn.hubo_commit and not conn.hubo_rollback


# ── proveedor: mensajes amigables y SOLO POST ────────────────────

def test_proveedor_fk_mensaje_amigable(monkeypatch):
    cliente = _admin()
    conn = _conexiones(monkeypatch, 'get_connection_tienda',
                       _pg_errores.ForeignKeyViolation())
    r = cliente.post('/proveedores/eliminar/5',
                     data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    assert ('danger', MENSAJE_FK) in _flashes(cliente)
    assert conn.hubo_rollback and not conn.hubo_commit


def test_proveedor_otro_error_mensaje_generico(monkeypatch):
    cliente = _admin()
    conn = _conexiones(monkeypatch, 'get_connection_tienda',
                       ValueError('detalle tecnico secreto'))
    r = cliente.post('/proveedores/eliminar/5',
                     data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    flashes = _flashes(cliente)
    assert ('danger', MENSAJE_GENERICO) in flashes
    assert not any('detalle tecnico' in msg for _, msg in flashes)
    assert conn.hubo_rollback and not conn.hubo_commit


def test_proveedor_get_rechazado_con_405():
    assert _admin().get('/proveedores/eliminar/5').status_code == 405


def test_proveedor_sin_csrf_rechazado_con_400():
    r = _admin().post('/proveedores/eliminar/5', data={})
    assert r.status_code == 400
