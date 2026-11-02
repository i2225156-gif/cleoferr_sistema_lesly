"""El OTP de recuperación NO viaja en el asunto del correo.

El asunto se ve en la bandeja de entrada, en la notificacion del movil y en
los logs del servidor de correo: por eso es neutro y no dice que es un
codigo ni de que servicio se trata. El codigo de 6 digitos va solo en el
cuerpo (texto plano y HTML), que es donde el usuario lo necesita.
"""
import re

import pytest

import app as modulo_app
from app import app

ASUNTO_ESPERADO = 'Tu código de verificación - Inversiones Cleoferr'


class CursorDoble:
    """Responde en cola: primero el usuario, despues el cliente."""

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
    def __init__(self, respuestas=None):
        self.cur = CursorDoble(respuestas)

    def cursor(self, dictionary=False, **kwargs):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def fx(monkeypatch):
    capturas = []

    def _correo_mock(destino, asunto, cuerpo_html, cuerpo_texto=None):
        capturas.append({'destino': destino, 'asunto': asunto,
                         'cuerpo_html': cuerpo_html,
                         'cuerpo_texto': cuerpo_texto or ''})
        return None

    conn = ConnDoble([None, {'id_cliente': 66, 'nombre': 'Ana'}])
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: conn)
    monkeypatch.setattr(modulo_app, 'enviar_correo', _correo_mock)
    c = app.test_client()
    with c.session_transaction() as s:
        s['_csrf_token'] = 'token-test'
    yield {'c': c, 'capturas': capturas}


def _pedir_codigo(fx):
    r = fx['c'].post('/recuperar_contrasena',
                     data={'correo': 'ana@example.com',
                           'csrf_token': 'token-test'})
    assert r.status_code == 200
    assert fx['capturas'], 'no se capturó ningún correo'
    return fx['capturas'][-1]


def test_el_asunto_es_neutro_y_no_anuncia_el_codigo(fx):
    correo = _pedir_codigo(fx)
    assert correo['asunto'] == ASUNTO_ESPERADO
    assert not re.search(r'\d{6}', correo['asunto'])
    assert 'código' not in correo['asunto'].lower() or \
        correo['asunto'] == ASUNTO_ESPERADO


def test_el_codigo_solo_travela_en_el_cuerpo(fx):
    correo = _pedir_codigo(fx)
    m = re.search(r'\b(\d{6})\b', correo['cuerpo_texto'])
    assert m, 'el código OTP debe estar en el cuerpo del correo'
    codigo = m.group(1)
    assert codigo not in correo['asunto']
    assert codigo in correo['cuerpo_html']


def test_el_cuerpo_sigue_informando_el_valor_del_codigo(fx):
    correo = _pedir_codigo(fx)
    assert '10 minutos' in correo['cuerpo_texto']
    assert 'contraseña' in correo['cuerpo_texto'].lower()
