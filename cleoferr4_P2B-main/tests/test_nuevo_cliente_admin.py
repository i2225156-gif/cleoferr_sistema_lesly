"""Fix del alta admin de clientes (POST /clientes/nuevo).

La BD exige apellido e id_tipo_documento NOT NULL (mismo esquema que
usan el registro web y el POS); el INSERT del panel no los enviaba y el
alta fallaba con NotNullViolation, enmascarado por el try/except como
flash de error. Este test nació en ROJO reproduciendo ese fallo; ahora
verifica el camino feliz dentro de una transacción con rollback sobre la
BD auth (nada persiste, datos 100% sintéticos).
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
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy)
    yield {'admin': _admin(), 'cur': cur,
           'correo': f"test-nc-{_uuid.uuid4().hex[:10]}@example.com"}
    proxy.rollback()
    raw.close()


def test_alta_admin_inserta_cliente_con_apellido_y_tipo_doc(fx_auth):
    correo = fx_auth['correo']
    r = fx_auth['admin'].post('/clientes/nuevo',
                              data={'nombre': 'Juan Carlos Garcia Lopez',
                                    'email': correo,
                                    'telefono': '987654321',
                                    'direccion': 'Av. Prueba 123',
                                    'contrasena': 'secreto123'},
                              follow_redirects=True)
    assert r.status_code == 200
    # Camino feliz: el alta ya NO cae en el except con NotNullViolation
    assert 'Cliente registrado correctamente' in r.get_data(as_text=True)

    cur = fx_auth['cur']
    cur.execute("SELECT nombre, apellido, id_tipo_documento, nro_documento,"
                " activo FROM cliente WHERE email = %s", (correo,))
    fila = cur.fetchone()
    assert fila, 'el cliente no se insertó'
    # Criterio del POS (registrar_pos): primera palabra = nombre, resto = apellido
    assert fila['nombre'] == 'Juan'
    assert fila['apellido'] == 'Carlos Garcia Lopez'
    # Criterio del registro web (registro_cliente): sin documento -> DNI (1)
    # con nro_documento NULL (nunca '': UNIQUE (id_tipo_documento, nro_documento))
    assert fila['id_tipo_documento'] == 1
    assert fila['nro_documento'] is None
    # El alta administrativa sigue verificando la cuenta (comportamiento actual)
    assert fila['activo'] is True
