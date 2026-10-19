"""RF1 y RF27: flujo de login/logout y recuperación de contraseña.

Cubre los huecos de la matriz RF con datos sintéticos y rollback (BD Auth,
sin datos personales; el envío de correo SIEMPRE mockeado):
- RF1: login staff correcto/incorrecto, anti-enumeración de credenciales,
  portal correcto por rol (un cliente no entra por /login), login de
  cliente verificado y no verificado, y logout que limpia la sesión.
- RF27: OTP válido cambia la contraseña (y la vieja deja de servir);
  expirado, reutilizado e incorrecto×5 la rechazan; anti-enumeración (un
  correo inexistente recibe la MISMA pantalla de verificación) y el
  fallback sin SMTP avisa sin romper nada.
- Los POSTs de auth van con su token CSRF (login, login_cliente,
  recuperar y restablecer están en _CSRF_PROTEGIDAS).
- BRECHA REPORTADA (sin test porque NO existe): el bloqueo de login por
  intentos fallidos. Las tablas (intento_login, ip_bloqueada) y columnas
  (usuario.bloqueado_hasta, intentos_fallidos) existen en la BD Auth pero
  NINGUNA ruta las usa: /login no cuenta intentos ni bloquea.
"""
import re

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app, bcrypt

CORREO_STAFF = 'staff-testrf1@invalido'
CORREO_CLI = 'cliente-testrf1@invalido'
CORREO_NOVER = 'nover-testrf1@invalido'
CORREO_FANTASMA = 'nadie-testrf1@invalido'
CLAVE = 'clave-test-123'


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


def _hash(pw):
    return bcrypt.generate_password_hash(pw).decode('utf-8')


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_auth.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)

    cur.execute("""
        INSERT INTO usuario (email, contrasena, id_rol, nombres,
                             fecha_registro, requiere_cambio_pwd, activo)
        VALUES (%s, %s, (SELECT id_rol FROM rol WHERE nombre='vendedor'),
                'Test Vendedor', NOW(), FALSE, TRUE)
    """, (CORREO_STAFF, _hash(CLAVE)))
    cur.execute("""
        INSERT INTO cliente (nombre, apellido, email, contrasena, activo,
                             id_tipo_documento, nro_documento, origen)
        VALUES ('Testf1', 'Cliente', %s, %s, TRUE, 1,
                right(md5(random()::text), 8), 'web'),
               ('Testf1', 'NoVer', %s, %s, FALSE, 1,
                right(md5(random()::text), 8), 'web')
    """, (CORREO_CLI, _hash(CLAVE), CORREO_NOVER, _hash(CLAVE)))

    # El correo NUNCA sale: mock que captura y devuelve 'ok'
    capturas = []

    def _correo_mock(destino, asunto, cuerpo_html, cuerpo_texto=None):
        capturas.append({'destino': destino, 'asunto': asunto})
        return 'ok'

    monkeypatch.setattr(modulo_app, 'enviar_correo', _correo_mock)
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy)
    yield {'cur': cur, 'capturas': capturas}
    proxy.rollback()
    raw.close()


def _cliente():
    """Test client con el token CSRF de sesión que esperan los POSTs."""
    c = app.test_client()
    with c.session_transaction() as s:
        s['_csrf_token'] = 'token-test'
    return c


def _post(c, ruta, **campos):
    campos['csrf_token'] = 'token-test'
    return c.post(ruta, data=campos)


def _codigo_de(fx):
    assert fx['capturas'], 'el mock del correo no capturó nada'
    m = re.search(r'\b(\d{6})\b', fx['capturas'][-1]['asunto'])
    assert m, fx['capturas'][-1]['asunto']
    return m.group(1)


def _pedir_codigo(fx, correo):
    fx['capturas'].clear()
    r = _post(_cliente(), '/recuperar_contrasena', correo=correo)
    assert r.status_code == 200
    return r


# ── RF1: login, roles y logout ─────────────────────────────────

def test_login_staff_correcto(fx):
    c = _cliente()
    r = _post(c, '/login', correo=CORREO_STAFF, clave=CLAVE)
    assert r.status_code == 302
    assert '/productos' in r.headers['Location']
    with c.session_transaction() as s:
        assert s['rol'] == 'vendedor'
        assert s['usuario_id'] is not None


def test_login_clave_incorrecta(fx):
    r = _post(_cliente(), '/login', correo=CORREO_STAFF, clave='otra')
    assert r.status_code == 200
    assert 'Credenciales incorrectas' in r.get_data(as_text=True)


def test_login_correo_inexistente_mismo_mensaje(fx):
    """Anti-enumeración: correo inexistente y clave errónea dan lo mismo."""
    r = _post(_cliente(), '/login', correo=CORREO_FANTASMA, clave='x')
    assert r.status_code == 200
    assert 'Credenciales incorrectas' in r.get_data(as_text=True)


def test_cliente_no_entra_por_portal_staff(fx):
    """Un cliente (vive en la tabla cliente) no tiene cuenta de staff:
    mensaje genérico de credenciales y SIN sesión iniciada."""
    c = _cliente()
    r = _post(c, '/login', correo=CORREO_CLI, clave=CLAVE)
    assert r.status_code == 200
    assert 'Credenciales incorrectas' in r.get_data(as_text=True)
    with c.session_transaction() as s:
        assert 'rol' not in s


def test_login_cliente_correcto(fx):
    c = _cliente()
    r = _post(c, '/login_cliente', correo=CORREO_CLI, contrasena=CLAVE)
    assert r.status_code == 302
    with c.session_transaction() as s:
        assert s['rol'] == 'cliente'


def test_login_cliente_no_verificado(fx):
    """1a: cuenta sin verificar (activo=FALSE) = credencial inválida,
    sin revelar el motivo ni abrir sesión."""
    c = _cliente()
    r = _post(c, '/login_cliente', correo=CORREO_NOVER, contrasena=CLAVE)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Credenciales incorrectas' in html
    assert 'verificada' not in html.lower()
    with c.session_transaction() as s:
        assert 'rol' not in s


def test_login_usuario_inactivo_rechazado(fx):
    """1a: un usuario de staff desactivado recibe el mismo mensaje
    genérico aunque su contraseña sea correcta."""
    fx['cur'].execute("UPDATE usuario SET activo = FALSE WHERE email=%s",
                      (CORREO_STAFF,))
    c = _cliente()
    r = _post(c, '/login', correo=CORREO_STAFF, clave=CLAVE)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Credenciales incorrectas' in html
    assert 'inactiv' not in html.lower()      # sin revelar el motivo
    with c.session_transaction() as s:
        assert 'rol' not in s


def test_post_sin_csrf_es_400(fx):
    r = _cliente().post('/login', data={'correo': CORREO_STAFF, 'clave': CLAVE})
    assert r.status_code == 400


def test_logout_limpia_la_sesion(fx):
    c = _cliente()
    _post(c, '/login', correo=CORREO_STAFF, clave=CLAVE)
    r = c.get('/logout')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']
    r2 = c.get('/productos', follow_redirects=False)
    assert r2.status_code == 302            # sin sesión: redirige al login
    assert '/login' in r2.headers['Location']


# ── RF27: recuperación de contraseña ────────────────────────────

def test_recuperar_y_restablecer(fx):
    _pedir_codigo(fx, CORREO_STAFF)
    codigo = _codigo_de(fx)
    r = _post(_cliente(), '/restablecer_contrasena', correo=CORREO_STAFF,
              codigo=codigo, nueva='nueva-clave-9', confirmar='nueva-clave-9')
    assert 'Contraseña actualizada' in r.get_data(as_text=True)
    fx['cur'].execute("SELECT contrasena FROM usuario WHERE email=%s",
                      (CORREO_STAFF,))
    assert bcrypt.check_password_hash(fx['cur'].fetchone()['contrasena'],
                                      'nueva-clave-9')
    # la clave vieja ya no sirve
    r = _post(_cliente(), '/login', correo=CORREO_STAFF, clave=CLAVE)
    assert 'Credenciales incorrectas' in r.get_data(as_text=True)


def test_codigo_expirado(fx):
    _pedir_codigo(fx, CORREO_CLI)
    codigo = _codigo_de(fx)
    fx['cur'].execute("UPDATE recuperacion_contrasena"
                      " SET expira_en = NOW() - INTERVAL '1 hour'")
    r = _post(_cliente(), '/restablecer_contrasena', correo=CORREO_CLI,
              codigo=codigo, nueva='nueva-clave-9', confirmar='nueva-clave-9')
    assert 'El código expiró' in r.get_data(as_text=True)
    fx['cur'].execute("SELECT contrasena FROM cliente WHERE email=%s",
                      (CORREO_CLI,))
    assert bcrypt.check_password_hash(fx['cur'].fetchone()['contrasena'], CLAVE)


def test_codigo_reutilizado(fx):
    _pedir_codigo(fx, CORREO_STAFF)
    codigo = _codigo_de(fx)
    r1 = _post(_cliente(), '/restablecer_contrasena', correo=CORREO_STAFF,
               codigo=codigo, nueva='nueva-clave-9', confirmar='nueva-clave-9')
    assert 'Contraseña actualizada' in r1.get_data(as_text=True)
    r2 = _post(_cliente(), '/restablecer_contrasena', correo=CORREO_STAFF,
               codigo=codigo, nueva='lo-que-sea-9', confirmar='lo-que-sea-9')
    assert 'No hay un código activo' in r2.get_data(as_text=True)


def test_codigo_incorrecto_cinco_veces_lo_invalida(fx):
    _pedir_codigo(fx, CORREO_STAFF)
    codigo_real = _codigo_de(fx)
    c = _cliente()
    for _ in range(5):
        r = _post(c, '/restablecer_contrasena', correo=CORREO_STAFF,
                  codigo='000000', nueva='nueva-clave-9', confirmar='nueva-clave-9')
    assert 'Demasiados intentos fallidos' in r.get_data(as_text=True)
    # el código REAL ya no sirve: quedó marcado usado tras los 5 intentos
    r = _post(c, '/restablecer_contrasena', correo=CORREO_STAFF,
              codigo=codigo_real, nueva='nueva-clave-9', confirmar='nueva-clave-9')
    assert 'No hay un código activo' in r.get_data(as_text=True)
    # y la contraseña no cambió
    fx['cur'].execute("SELECT contrasena FROM usuario WHERE email=%s",
                      (CORREO_STAFF,))
    assert bcrypt.check_password_hash(fx['cur'].fetchone()['contrasena'], CLAVE)


def test_anti_enumeracion_correo_inexistente(fx):
    """Correo inexistente: misma pantalla de verificación, sin envío y
    sin revelar si la cuenta existe."""
    r = _pedir_codigo(fx, CORREO_FANTASMA)
    assert not fx['capturas']              # no salió ningún correo
    html = r.get_data(as_text=True)
    assert 'name="codigo"' in html         # la misma pantalla de verificación
    assert CORREO_FANTASMA in html
    assert 'no existe' not in html.lower()


def test_sin_smtp_avisa_sin_romper(fx, monkeypatch):
    monkeypatch.setattr(modulo_app, 'enviar_correo',
                        lambda *a, **k: 'smtp_no_configurado')
    r = _post(_cliente(), '/recuperar_contrasena', correo=CORREO_STAFF)
    assert 'El envío de correos no está configurado' in r.get_data(as_text=True)
