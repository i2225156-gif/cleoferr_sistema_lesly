"""C1: cambiar_clave debe actualizar la tabla del ROL de la sesión.

- Un CLIENTE con id que colisiona con un usuario de staff NO puede
  sobrescribir la contraseña del staff (regresión del bug C1): su cambio
  aplica a su propia fila de `cliente`.
- Exige la clave ACTUAL: incorrecta o ausente → rechazo con mensaje claro
  y sin ningún cambio en la BD.
- Staff (administrador/vendedor) cambia su clave en `usuario`.
- Sin sesión → redirect al login; sin token CSRF → 400.
- Si la cuenta no existe no se muestra "actualizada".

Datos sintéticos en la BD Auth dentro de una transacción con rollback.
"""
import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app, bcrypt

CORREO_STAFF = 'staff-testc1@invalido'
CORREO_CLI = 'cli-testc1@invalido'
CLAVE_STAFF = 'ClaveOriginal1'
CLAVE_CLI = 'SuClave123'
ID_STAFF = 990002          # usuario y cliente colisionan a propósito
ID_CLI = 990010            # cliente sin colisión


class ConnProxy:
    """commit() no-op: la ruta cree que commitea; el test hace rollback."""

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


def _hash(pw):
    return bcrypt.generate_password_hash(pw).decode('utf-8')


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_auth.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute("""
        INSERT INTO usuario (id_usuario, email, contrasena, id_rol, nombres, activo)
        VALUES (%s, %s, %s, (SELECT id_rol FROM rol WHERE nombre='vendedor'),
                'Victima Staff', TRUE)
    """, (ID_STAFF, CORREO_STAFF, _hash(CLAVE_STAFF)))
    cur.execute("""
        INSERT INTO cliente (id_cliente, nombre, apellido, email, telefono, direccion,
                             contrasena, activo, id_tipo_documento, nro_documento)
        VALUES (%s, 'Colision', 'Cliente', %s, '987654321', 'Calle Uno',
                %s, TRUE, 1, '99000201'),
               (%s, 'Normal', 'Cliente', %s, '987654321', 'Calle Dos',
                %s, TRUE, 1, '99000202')
    """, (ID_STAFF, CORREO_CLI + 'x', _hash(CLAVE_CLI),
          ID_CLI, CORREO_CLI, _hash(CLAVE_CLI)))
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy}
    proxy.rollback()
    raw.close()


def _sesion(rol, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


def _post(c, actual=None, nueva='NuevaClave9', confirmar='NuevaClave9',
          follow=False):
    campos = {'csrf_token': 'token-test', 'nueva': nueva, 'confirmar': confirmar}
    if actual is not None:
        campos['actual'] = actual
    return c.post('/cambiar_clave', data=campos, follow_redirects=follow)


def _hash_de(cur, tabla, id_col, id_val):
    cur.execute(f"SELECT contrasena FROM {tabla} WHERE {id_col} = %s", (id_val,))
    return cur.fetchone()['contrasena']


def _responde(cur, tabla, id_col, id_val, clave):
    return bcrypt.check_password_hash(_hash_de(cur, tabla, id_col, id_val), clave)


# ── C1: la tabla que se actualiza es la del ROL de la sesión ──────────

def test_cliente_con_id_colisionado_no_toca_al_staff(fx):
    """El bug original: un cliente (id == id de un usuario) sobrescribía
    la contraseña del staff y entraba al panel. Ahora su cambio aplica a
    su propia fila de cliente y el staff queda intacto."""
    r = _post(_sesion('cliente', ID_STAFF), actual=CLAVE_CLI)
    cur = fx['cur']
    assert r.status_code == 302
    assert _responde(cur, 'cliente', 'id_cliente', ID_STAFF, 'NuevaClave9'), \
        'la clave del CLIENTE debe cambiar'
    assert _responde(cur, 'usuario', 'id_usuario', ID_STAFF, CLAVE_STAFF), \
        'la clave del STAFF NO debe cambiar (regresión C1)'


def test_cliente_cambia_su_propia_clave(fx):
    c = _sesion('cliente', ID_CLI)
    r = _post(c, actual=CLAVE_CLI)
    assert r.status_code == 302
    cur = fx['cur']
    assert _responde(cur, 'cliente', 'id_cliente', ID_CLI, 'NuevaClave9')
    assert not _responde(cur, 'cliente', 'id_cliente', ID_CLI, CLAVE_CLI)
    # la clave vieja ya no sirve para entrar
    cc = app.test_client()
    with cc.session_transaction() as s:
        s['_csrf_token'] = 'token-test'
    campos = {'csrf_token': 'token-test', 'correo': CORREO_CLI, 'contrasena': CLAVE_CLI}
    assert 'Credenciales incorrectas' in cc.post('/login_cliente', data=campos).get_data(as_text=True)


def test_staff_cambia_su_clave(fx):
    c = _sesion('vendedor', ID_STAFF)
    r = _post(c, actual=CLAVE_STAFF)
    assert r.status_code == 302
    cur = fx['cur']
    assert _responde(cur, 'usuario', 'id_usuario', ID_STAFF, 'NuevaClave9')
    assert not _responde(cur, 'usuario', 'id_usuario', ID_STAFF, CLAVE_STAFF)
    # el cliente colisionante no sufrió ningún cambio
    assert _responde(cur, 'cliente', 'id_cliente', ID_STAFF, CLAVE_CLI)
    cur.execute("SELECT fecha_ultimo_cambio_pwd FROM usuario WHERE id_usuario = %s",
                (ID_STAFF,))
    assert cur.fetchone()['fecha_ultimo_cambio_pwd'] is not None


# ── exigir la clave actual ────────────────────────────────────────────

def test_clave_actual_incorrecta_rechaza_sin_cambiar(fx):
    c = _sesion('cliente', ID_CLI)
    r = _post(c, actual='_equivocada')
    html = r.get_data(as_text=True)
    assert 'actual no es correcta' in html
    cur = fx['cur']
    assert _responde(cur, 'cliente', 'id_cliente', ID_CLI, CLAVE_CLI)
    assert not _responde(cur, 'cliente', 'id_cliente', ID_CLI, 'NuevaClave9')


def test_sin_clave_actual_rechaza(fx):
    c = _sesion('vendedor', ID_STAFF)
    r = _post(c)                      # sin campo 'actual'
    assert 'actual' in r.get_data(as_text=True).lower()
    cur = fx['cur']
    assert _responde(cur, 'usuario', 'id_usuario', ID_STAFF, CLAVE_STAFF)


def test_clave_nueva_muy_corta_rechaza(fx):
    c = _sesion('vendedor', ID_STAFF)
    r = _post(c, actual=CLAVE_STAFF, nueva='abc', confirmar='abc')
    assert 'al menos 6' in r.get_data(as_text=True)
    cur = fx['cur']
    assert _responde(cur, 'usuario', 'id_usuario', ID_STAFF, CLAVE_STAFF)


def test_no_coinciden_rechaza(fx):
    c = _sesion('vendedor', ID_STAFF)
    r = _post(c, actual=CLAVE_STAFF, nueva='NuevaClave9', confirmar='OtraCosa8')
    assert 'no coinciden' in r.get_data(as_text=True)
    cur = fx['cur']
    assert _responde(cur, 'usuario', 'id_usuario', ID_STAFF, CLAVE_STAFF)


# ── cuenta inexistente, sesión y CSRF ────────────────────────────────

def test_cuenta_inexistente_no_muestra_exito(fx):
    c = _sesion('cliente', 990099)     # id sin fila en cliente
    r = _post(c, actual='loquesea1', follow=True)
    html = r.get_data(as_text=True)
    assert 'Contraseña actualizada' not in html


def test_sin_sesion_redirige_al_login(fx):
    r = app.test_client().get('/cambiar_clave')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']
    # POST anónimo: el CSRF corre ANTES que la sesión (fail closed) → 400
    r2 = app.test_client().post('/cambiar_clave',
                                data={'actual': 'x', 'nueva': 'NuevaClave9',
                                      'confirmar': 'NuevaClave9',
                                      'csrf_token': 'token-test'})
    assert r2.status_code == 400


def test_sin_csrf_es_400(fx):
    c = _sesion('cliente', ID_CLI)
    r = c.post('/cambiar_clave',
               data={'actual': CLAVE_CLI, 'nueva': 'NuevaClave9',
                     'confirmar': 'NuevaClave9'})
    assert r.status_code == 400
