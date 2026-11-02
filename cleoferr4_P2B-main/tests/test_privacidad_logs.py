"""Privacidad de los logs: ni correos, ni DNI/RUC, ni telefonos, ni nombres.

Captura los logs (caplog) de los tres flujos que reputaciónan el dato
personal: login fallido, registro (cuenta ya existente) y recuperacion de
contrasena (envio del codigo). El log puede llevar identificadores
(id_cliente, id_usuario, tabla), NUNCA el correo ni el resto de datos
personales; la fabrica de registros es la ultima barrera.
"""
import logging
import re
import smtplib

import pytest

import app as modulo_app
from app import app

_PREGUNTA = re.compile(r'(\d+)\s*([+-])\s*(\d+)\?')


class CursorDoble:
    """Registra cada consulta y sirve respuestas en cola."""

    def __init__(self, respuestas=None):
        self.respuestas = list(respuestas or [])
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))

    def fetchone(self):
        return self.respuestas.pop(0) if self.respuestas else None

    def fetchall(self):
        return []


class ConnDoble:
    """Contrato de la conexion AUTH usada por login/registro/recuperacion."""

    def __init__(self, respuestas=None):
        self.cur = CursorDoble(respuestas)
        self.commits = 0

    def cursor(self, dictionary=False, **kwargs):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


class _SMTP_OK:
    """SMTP de mentira: el envio sale bien."""

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, usuario, clave):
        pass

    def sendmail(self, emisor, destinatarios, mensaje):
        return {}

    def quit(self):
        pass


class _SMTP_RECHAZA(_SMTP_OK):
    """SMTP de mentira: el servidor rechaza al destinatario (su error lo
    incluye: el log NO puede repetirlo)."""

    def sendmail(self, emisor, destinatarios, mensaje):
        raise smtplib.SMTPRecipientsRefused(
            {destinatarios[0]: (550, b'no such user')})


@pytest.fixture()
def fx(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    conn = ConnDoble()
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: conn)
    c = app.test_client()
    with c.session_transaction() as s:
        s['_csrf_token'] = 'token-test'
    yield {'c': c, 'conn': conn, 'caplog': caplog}


def _captcha(cliente):
    """Resuelve el captcha aritmetico leyendo la pregunta del formulario."""
    html = cliente.get('/login_cliente').get_data(as_text=True)
    m = _PREGUNTA.search(html)
    assert m, 'no se encontro la pregunta del captcha en el formulario'
    a, b = int(m.group(1)), int(m.group(3))
    return str(a + b if m.group(2) == '+' else a - b)


# ── login fallido ───────────────────────────────────────────────

def test_login_cliente_fallido_no_registra_el_correo(fx):
    correo = 'login.cliente@example.com'
    fila = {'id_cliente': 77, 'nombre': 'Ana', 'apellido': 'Prueba',
            'email': correo, 'activo': True, 'intentos_fallidos': 4,
            'bloqueado_hasta': None,
            'contrasena': modulo_app.bcrypt.generate_password_hash('correcta').decode()}
    fx['conn'].cur.respuestas = [fila]

    r = fx['c'].post('/login_cliente',
                     data={'correo': correo, 'contrasena': 'incorrecta',
                           'csrf_token': 'token-test'})
    assert r.status_code == 200
    assert 'Credenciales incorrectas' in r.get_data(as_text=True)

    logs = fx['caplog'].text
    assert correo not in logs
    assert '77' in logs                    # el id si puede quedar


def test_login_staff_fallido_no_registra_el_correo(fx):
    correo = 'login.staff@example.com'
    fila = {'id_usuario': 88, 'nombres': 'Vendedor Prueba', 'email': correo,
            'rol': 'vendedor', 'activo': True, 'intentos_fallidos': 4,
            'bloqueado_hasta': None,
            'contrasena': modulo_app.bcrypt.generate_password_hash('correcta').decode()}
    fx['conn'].cur.respuestas = [fila]

    r = fx['c'].post('/login', data={'correo': correo, 'clave': 'incorrecta',
                                     'csrf_token': 'token-test'})
    assert r.status_code == 200
    assert 'Credenciales incorrectas' in r.get_data(as_text=True)

    logs = fx['caplog'].text
    assert correo not in logs
    assert '88' in logs


# ── registro ────────────────────────────────────────────────────

def test_registro_bloqueado_no_registra_el_correo(fx):
    correo = 'registro.bloqueado@example.com'
    respuesta_captcha = _captcha(fx['c'])
    fx['conn'].cur.respuestas = [
        {'id_tipo_documento': 1},
        {'n': 4},
        {'id_cliente': 55, 'activo': True, 'contrasena': 'hash'},
    ]

    r = fx['c'].post('/registro_cliente', data={
        'csrf_token': 'token-test',
        'nombre': 'Ana', 'apellido': 'Quispe Rojas',
        'correo': correo, 'telefono': '987654321',
        'tipo_documento': 'dni', 'nro_documento': '',
        'direccion': 'Av. Prueba 123',
        'contrasena': 'clave-123', 'confirmar': 'clave-123',
        'acepta_privacidad': '1',
        'captcha_respuesta': respuesta_captcha,
    })
    assert r.status_code == 200

    logs = fx['caplog'].text
    assert correo not in logs
    assert '55' in logs                    # se identifica con el id


# ── recuperacion de contrasena ──────────────────────────────────

def test_recuperacion_contrasena_no_registra_el_correo(fx, monkeypatch):
    correo = 'recuperacion@example.com'
    monkeypatch.setenv('SMTP_USER', 'remitente@example.com')
    monkeypatch.setenv('SMTP_PASS', 'abcd efgh ijkl mnop')
    monkeypatch.setattr(smtplib, 'SMTP_SSL', _SMTP_OK)
    monkeypatch.setattr(smtplib, 'SMTP', _SMTP_OK)
    fx['conn'].cur.respuestas = [None, {'id_cliente': 66, 'nombre': 'Ana'}]

    r = fx['c'].post('/recuperar_contrasena',
                     data={'correo': correo, 'csrf_token': 'token-test'})
    assert r.status_code == 200

    logs = fx['caplog'].text
    assert correo not in logs
    assert 'Correo enviado' in logs        # el envio si se registra


def test_recuperacion_contrasena_no_registra_el_correo_si_smtp_lo_rechaza(fx, monkeypatch):
    correo = 'recuperacion.rechazada@example.com'
    monkeypatch.setenv('SMTP_USER', 'remitente@example.com')
    monkeypatch.setenv('SMTP_PASS', 'abcd efgh ijkl mnop')
    monkeypatch.setattr(smtplib, 'SMTP_SSL', _SMTP_RECHAZA)
    monkeypatch.setattr(smtplib, 'SMTP', _SMTP_RECHAZA)
    fx['conn'].cur.respuestas = [None, {'id_cliente': 66, 'nombre': 'Ana'}]

    r = fx['c'].post('/recuperar_contrasena',
                     data={'correo': correo, 'csrf_token': 'token-test'})
    assert r.status_code == 200

    logs = fx['caplog'].text
    assert correo not in logs
    assert 'No se pudo enviar el correo' in logs


def test_registro_con_excepcion_de_correo_no_da_500_ni_registra_pii(fx, monkeypatch):
    correo = 'registro.fallo@example.com'
    respuesta_captcha = _captcha(fx['c'])
    fx['conn'].cur.respuestas = [
        {'id_tipo_documento': 1},
        {'n': 4},
        None,
        {'id_cliente': 55},
    ]

    def falla_correo(*args, **kwargs):
        raise RuntimeError('SMTP: registro.fallo@example.com Ana 987654321')

    monkeypatch.setattr(modulo_app, 'enviar_correo', falla_correo)
    r = fx['c'].post('/registro_cliente', data={
        'csrf_token': 'token-test',
        'nombre': 'Ana', 'apellido': 'Prueba',
        'correo': correo, 'telefono': '987654321',
        'tipo_documento': 'dni', 'nro_documento': '',
        'direccion': 'Av. Prueba 123',
        'contrasena': 'clave-123', 'confirmar': 'clave-123',
        'acepta_privacidad': '1', 'captcha_respuesta': respuesta_captcha,
    })

    assert r.status_code == 200
    assert 'No se pudo enviar el correo' in r.get_data(as_text=True)
    logs = fx['caplog'].text
    assert 'Error de envío de correo en registro_cliente' in logs
    assert correo not in logs
    assert 'Ana' not in logs
    assert '987654321' not in logs


def test_recuperacion_con_excepcion_de_correo_no_da_500_ni_registra_pii(fx, monkeypatch):
    correo = 'recuperacion.fallo@example.com'
    fx['conn'].cur.respuestas = [None, {'id_cliente': 66, 'nombre': 'Ana'}]

    def falla_correo(*args, **kwargs):
        raise RuntimeError('SMTP: recuperacion.fallo@example.com Ana 987654321')

    monkeypatch.setattr(modulo_app, 'enviar_correo', falla_correo)
    r = fx['c'].post('/recuperar_contrasena',
                     data={'correo': correo, 'csrf_token': 'token-test'})

    assert r.status_code == 200
    assert 'No se pudo enviar el correo' in r.get_data(as_text=True)
    logs = fx['caplog'].text
    assert 'Error de envío de correo en recuperar_contrasena' in logs
    assert correo not in logs
    assert 'Ana' not in logs
    assert '987654321' not in logs


# ── la barrera global ───────────────────────────────────────────

def test_el_filtro_enmascara_cualquier_correo_del_log(fx):
    logging.getLogger('prueba').info("prueba %s", 'persona@example.com')
    assert 'persona@example.com' not in fx['caplog'].text
    assert '[correo]' in fx['caplog'].text

    app.logger.error("otra prueba %s", 'otra.persona@example.com')
    assert 'otra.persona@example.com' not in fx['caplog'].text


def test_el_filtro_enmascara_el_traceback_con_datos(caplog):
    """El traceback de psycopg2 repite el dato que violo la restriccion."""
    caplog.set_level(logging.INFO)
    try:
        raise ValueError("duplicate key: persona@example.com nro_documento=87654321")
    except Exception as e:
        app.logger.exception("Error de prueba: %s", e)
    assert 'persona@example.com' not in caplog.text
    assert '87654321' not in caplog.text
