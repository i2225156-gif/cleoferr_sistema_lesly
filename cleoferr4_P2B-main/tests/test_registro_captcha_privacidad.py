"""Captcha + honeypot + privacidad del registro web (sin BD real).

registro_cliente valida ahora tres salvaguardas ANTES de cualquier
consulta a la BD o envío de correo:

  * honeypot «sitio_web» (bots) → mensaje genérico;
  * captcha aritmético de un solo uso (services/captcha.py): en sesión
    solo vive el HMAC de la respuesta —la cookie es legible—;
  * casilla OBLIGATORIA de la Política de Privacidad (Ley 29733).

La BD Auth se sustituye por un doble que graba cada consulta: si una
validación falla, la doble conexión no debe recibir NINGUNA. El guardado
tolerante (migración auth_04 sin aplicar) se prueba con y sin las
columnas de privacidad.
"""
import hashlib
import hmac
import re

import pytest

from flask import session

import app as modulo_app
from app import app
from services import captcha

MSG_CAPTCHA    = 'La respuesta de verificación no es correcta. Inténtalo de nuevo.'
MSG_PRIVACIDAD = 'Debes aceptar la Política de Privacidad para registrarte.'

_PREGUNTA = re.compile(r'(\d+)\s*([+-])\s*(\d+)\?')


class CursorDoble:
    """Registra cada (sql, params) ejecutado y sirve respuestas en cola."""

    def __init__(self, respuestas=None):
        self.respuestas = list(respuestas or [])
        self.consultas  = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))

    def fetchone(self):
        return self.respuestas.pop(0) if self.respuestas else None


class ConnDoble:
    """Contrato de la conexión AUTH usado por registro_cliente."""

    def __init__(self, respuestas=None):
        self.cur     = CursorDoble(respuestas)
        self.commits = 0

    def cursor(self, dictionary=False, **kwargs):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _resolver_captcha(cliente):
    """Pide el formulario (cada render genera un captcha nuevo) y calcula
    la respuesta leyendo la pregunta del HTML: la sesión solo guarda su
    HMAC, nunca la respuesta en claro."""
    html = cliente.get('/login_cliente').get_data(as_text=True)
    m = _PREGUNTA.search(html)
    assert m, 'no se encontró la pregunta del captcha en el formulario'
    a, b = int(m.group(1)), int(m.group(3))
    return str(a + b if m.group(2) == '+' else a - b)


def _datos_registro(**extra):
    datos = {'csrf_token': 'token-test',
             'nombre': 'Ana', 'apellido': 'Quispe Rojas',
             'correo': 'ana@test.example', 'telefono': '987654321',
             'tipo_documento': 'dni', 'nro_documento': '',
             'direccion': 'Av. Prueba 123',
             'contrasena': 'clave-123', 'confirmar': 'clave-123',
             'acepta_privacidad': '1'}
    datos.update(extra)
    return datos


def _respuestas_bd(n_columnas=4, existente=None):
    """Cola de fetchone: tipo de documento, columnas de privacidad,
    cliente existente y RETURNING del INSERT."""
    return [{'id_tipo_documento': 1},
            {'n': n_columnas},
            existente,
            {'id_cliente': 55}]


@pytest.fixture()
def fx(monkeypatch):
    conn = ConnDoble()
    # El correo de verificación NUNCA sale: doble silencioso.
    monkeypatch.setattr(modulo_app, 'enviar_correo', lambda *a, **k: None)
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: conn)
    c = app.test_client()
    with c.session_transaction() as s:
        s['_csrf_token'] = 'token-test'
    yield {'c': c, 'conn': conn}


def _post(fx, datos):
    return fx['c'].post('/registro_cliente', data=datos,
                        follow_redirects=True).get_data(as_text=True)


# ── formulario: captcha, honeypot y casillas ────────────────────

def test_formulario_muestra_captcha_honeypot_y_casillas(fx):
    html = fx['c'].get('/login_cliente').get_data(as_text=True)
    assert '¿Cuánto es' in html                 # pregunta del captcha
    assert 'captcha_respuesta' in html          # input numérico
    assert 'sitio_web' in html                 # honeypot oculto
    assert 'acepta_privacidad' in html          # casilla obligatoria
    assert 'acepta_publicidad' in html          # casilla opcional
    assert '/privacidad' in html                # enlace en el pie


# ── captcha: correcto / incorrecto / expirado / reutilizado ────

def test_captcha_correcto_llega_hasta_verificar(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    html = _post(fx, _datos_registro(captcha_respuesta=_resolver_captcha(fx['c'])))
    assert 'Verifica tu correo' in html        # pasó captcha, BD y correo
    assert fx['conn'].commits == 1
    assert any(sql.lstrip().startswith('INSERT INTO verificacion_cuenta')
               for sql, _ in fx['conn'].cur.consultas)


def test_captcha_incorrecto_rechazado_sin_tocar_bd(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    html = _post(fx, _datos_registro(captcha_respuesta='99'))   # > 9+9: imposible
    assert MSG_CAPTCHA in html
    assert fx['conn'].cur.consultas == []      # ni una consulta a la BD


def test_captcha_expirado_rechazado(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    respuesta = _resolver_captcha(fx['c'])
    with fx['c'].session_transaction() as s:
        s['captcha_registro'] = {**s['captcha_registro'], 'expira': 0.0}
    html = _post(fx, _datos_registro(captcha_respuesta=respuesta))
    assert MSG_CAPTCHA in html
    assert fx['conn'].cur.consultas == []


def test_captcha_reutilizado_rechazado(fx):
    """Un solo uso: tras un registro exitoso, la misma respuesta ya no
    vale (el captcha se consumió)."""
    fx['conn'].cur.respuestas = _respuestas_bd()
    respuesta = _resolver_captcha(fx['c'])
    html = _post(fx, _datos_registro(captcha_respuesta=respuesta))
    assert 'Verifica tu correo' in html
    consultas_antes = len(fx['conn'].cur.consultas)
    html = _post(fx, _datos_registro(captcha_respuesta=respuesta))
    assert MSG_CAPTCHA in html
    assert len(fx['conn'].cur.consultas) == consultas_antes   # ni una más


def test_captcha_no_numerico_rechazado(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    html = _post(fx, _datos_registro(captcha_respuesta='cuatro'))
    assert MSG_CAPTCHA in html
    assert fx['conn'].cur.consultas == []


def test_sin_captcha_en_sesion_rechazado(fx):
    """POST directo sin generar captcha (o sesión nueva): rechazado."""
    fx['conn'].cur.respuestas = _respuestas_bd()
    html = _post(fx, _datos_registro(captcha_respuesta='7'))
    assert MSG_CAPTCHA in html
    assert fx['conn'].cur.consultas == []


def test_captcha_regenerado_tras_un_error(fx):
    """Cada render del formulario genera un captcha NUEVO: el nonce de
    sesión cambia aunque la pregunta pudiera repetirse."""
    fx['c'].get('/login_cliente')
    with fx['c'].session_transaction() as s:
        nonce_antes = s['captcha_registro']['nonce']
    _post(fx, _datos_registro(captcha_respuesta='99'))       # falla
    with fx['c'].session_transaction() as s:
        assert s['captcha_registro']['nonce'] != nonce_antes


# ── honeypot ───────────────────────────────────────────────────

def test_honeypot_con_contenido_rechaza_registro(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    html = _post(fx, _datos_registro(sitio_web='http://spam.bot',
                                     captcha_respuesta=_resolver_captcha(fx['c'])))
    assert MSG_CAPTCHA in html                 # mensaje genérico, no delata
    assert fx['conn'].cur.consultas == []


def test_honeypot_vacio_no_estorba(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    html = _post(fx, _datos_registro(sitio_web='',
                                     captcha_respuesta=_resolver_captcha(fx['c'])))
    assert 'Verifica tu correo' in html


# ── casilla de privacidad (Ley 29733) ───────────────────────────

def test_sin_casilla_obligatoria_rechazado(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    datos = _datos_registro(captcha_respuesta=_resolver_captcha(fx['c']))
    datos.pop('acepta_privacidad')
    html = _post(fx, datos)
    assert MSG_PRIVACIDAD in html
    assert fx['conn'].cur.consultas == []


def test_con_casilla_registra_la_aceptacion(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    html = _post(fx, _datos_registro(captcha_respuesta=_resolver_captcha(fx['c'])))
    assert 'Verifica tu correo' in html
    insert = [c for c in fx['conn'].cur.consultas
              if c[0].lstrip().startswith('INSERT INTO cliente')][0]
    sql, params = insert
    assert 'acepta_privacidad' in sql and 'fecha_acepta_privacidad' in sql
    assert "'1.0'" in sql                      # version_privacidad
    assert params[-1] is False                 # sin casilla de publicidad


def test_casilla_publicidad_se_registra(fx):
    fx['conn'].cur.respuestas = _respuestas_bd()
    _post(fx, _datos_registro(acepta_publicidad='1',
                              captcha_respuesta=_resolver_captcha(fx['c'])))
    insert = [c for c in fx['conn'].cur.consultas
              if c[0].lstrip().startswith('INSERT INTO cliente')][0]
    assert insert[1][-1] is True


def test_cuenta_pendiente_actualiza_la_aceptacion(fx):
    """Fila reutilizada (pendiente de OTP): el UPDATE también deja la
    constancia del consentimiento."""
    fx['conn'].cur.respuestas = _respuestas_bd(
        existente={'id_cliente': 77, 'activo': False, 'contrasena': None})
    html = _post(fx, _datos_registro(captcha_respuesta=_resolver_captcha(fx['c'])))
    assert 'Verifica tu correo' in html
    update = [c for c in fx['conn'].cur.consultas
              if c[0].lstrip().startswith('UPDATE cliente')][0]
    assert 'acepta_privacidad=TRUE' in update[0]


# ── tolerancia: migración auth_04 sin aplicar ────────────────────

def test_sin_columnas_privacidad_el_registro_sigue(fx):
    """B6: si la BD aún no tiene las columnas, el registro NO se rompe;
    se guarda sin esos campos."""
    fx['conn'].cur.respuestas = _respuestas_bd(n_columnas=0)
    html = _post(fx, _datos_registro(captcha_respuesta=_resolver_captcha(fx['c'])))
    assert 'Verifica tu correo' in html
    insert = [c for c in fx['conn'].cur.consultas
              if c[0].lstrip().startswith('INSERT INTO cliente')][0]
    assert 'acepta_privacidad' not in insert[0]
    assert fx['conn'].commits == 1


# ── la respuesta del captcha jamás viaja en claro ───────────────

def test_sesion_solo_guarda_hmac_del_captcha():
    """La cookie de sesión es legible: la respuesta solo puede existir
    dentro del HMAC (nonce + respuesta con la SECRET_KEY)."""
    with app.test_request_context():
        pregunta = captcha.generar()
        m = _PREGUNTA.search(pregunta)
        respuesta = int(m.group(1)) + int(m.group(3)) \
            if m.group(2) == '+' else int(m.group(1)) - int(m.group(3))
        datos = dict(session[captcha.CLAVE_SESION])
        assert set(datos) == {'mac', 'nonce', 'expira'}
        mac = hmac.new(app.secret_key.encode('utf-8'),
                       f"{datos['nonce']}:{respuesta}".encode('utf-8'),
                       hashlib.sha256).hexdigest()
        assert datos['mac'] == mac                       # es el HMAC exacto
        assert all(v != respuesta and v != str(respuesta)
                   for v in datos.values())             # nada en claro


def test_validar_acepta_espacios_y_rechaza_resto():
    """Unidad de services.captcha: espacios alrededor sí; no numérico,
    incorrecto y reintentos contra el mismo desafío, no."""
    with app.test_request_context():
        pregunta = captcha.generar()
        m = _PREGUNTA.search(pregunta)
        respuesta = int(m.group(1)) + int(m.group(3)) \
            if m.group(2) == '+' else int(m.group(1)) - int(m.group(3))
        assert captcha.validar(f'  {respuesta}  ') is True

    with app.test_request_context():
        captcha.generar()
        assert captcha.validar('cuatro') is False      # no numérico (consume)
    with app.test_request_context():
        captcha.generar()
        assert captcha.validar('99') is False          # incorrecto (consume)
    with app.test_request_context():
        pregunta = captcha.generar()
        m = _PREGUNTA.search(pregunta)
        respuesta = int(m.group(1)) + int(m.group(3)) \
            if m.group(2) == '+' else int(m.group(1)) - int(m.group(3))
        equivocada = str(respuesta + 1) if respuesta < 18 else '0'
        assert captcha.validar(equivocada) is False    # falla: se consume
        assert captcha.validar(str(respuesta)) is False   # ya usado


# ── ruta pública /privacidad ────────────────────────────────────

def test_privacidad_es_publica_y_completa():
    html = app.test_client().get('/privacidad').get_data(as_text=True)
    assert 'INVERSIONES CLEOFERR E.I.R.L.' in html
    assert '20605977074' in html
    assert '29733' in html
    assert 'ARCO' in html
    assert '[correo de contacto]' in html
    assert 'Versión 1.0' in html or 'versión 1.0' in html
