"""FASE 2 — validaciones de servidor del REGISTRO DE CLIENTE (web).

El registro ya era el mejor validado del sistema; ahora sus nombres son
de PERSONA (sin dígitos, con tildes y ñ) y el correo exige formato.
El resto de reglas (dirección >= 5, contraseñas iguales y >= 6,
documento opcional validado) ya existía y queda intacta.
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


def _anonimo():
    c = app.test_client()
    with c.session_transaction() as s:
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_auth.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    # El correo de verificación NUNCA sale: mock silencioso
    monkeypatch.setattr(modulo_app, 'enviar_correo',
                        lambda *a, **k: 'ok')
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy)
    yield {'c': _anonimo(), 'cur': cur}
    proxy.rollback()
    raw.close()


def _registrar(fx, **campos):
    datos = {'csrf_token': 'token-test',
             'nombre': 'José', 'apellido': 'Ñúñez Pérez',
             'correo': 'zzqreg@mail.invalid', 'telefono': '987654321',
             'direccion': 'Av. Prueba 123', 'contrasena': 'clave-ok-9',
             'confirmar': 'clave-ok-9'}
    datos.update(campos)
    return fx['c'].post('/registro_cliente', data=datos,
                        follow_redirects=True).get_data(as_text=True)


def _creado(fx, correo):
    fx['cur'].execute("SELECT nombre, apellido FROM cliente WHERE email=%s",
                      (correo,))
    return fx['cur'].fetchone()


# ── nombres de persona ───────────────────────────────────────────

def test_nombre_con_digitos_rechazado(fx):
    html = _registrar(fx, nombre='Jose123')
    assert 'El campo «nombre» solo admite letras' in html
    assert _creado(fx, 'zzqreg@mail.invalid') is None


def test_apellido_con_digitos_rechazado(fx):
    html = _registrar(fx, apellido='Perez 42')
    assert 'El campo «apellido» solo admite letras' in html


def test_nombre_solo_espacios_rechazado(fx):
    html = _registrar(fx, nombre='   ')
    assert 'El campo «nombre» es obligatorio' in html


def test_tildes_y_enie_aceptadas(fx):
    """Camino feliz con tildes y ñ: se guarda y queda pendiente de OTP."""
    html = _registrar(fx)
    assert 'código' in html or 'verific' in html.lower()   # flujo OTP
    fila = _creado(fx, 'zzqreg@mail.invalid')
    assert fila is not None
    assert fila['nombre'] == 'José' and fila['apellido'] == 'Ñúñez Pérez'


# ── correo ───────────────────────────────────────────────────────

def test_correo_sin_formato_rechazado(fx):
    html = _registrar(fx, correo='no-es-correo')
    assert 'no tiene un formato válido' in html
    assert _creado(fx, 'no-es-correo') is None


def test_correo_con_mayusculas_normalizado(fx):
    html = _registrar(fx, correo='ZZQREG@MAIL.INVALID')
    fila = _creado(fx, 'zzqreg@mail.invalid')
    assert fila is not None      # normalizado a minúsculas, sin duplicado


# ── longitudes ───────────────────────────────────────────────────

def test_telefono_excesivo_rechazado(fx):
    html = _registrar(fx, telefono='9' * 21)
    assert 'no puede superar 20 caracteres' in html


def test_direccion_excesiva_rechazada(fx):
    html = _registrar(fx, direccion='d' * 201)
    assert 'no puede superar 200 caracteres' in html


# ── reglas preexistentes intactas ────────────────────────────────

def test_direccion_corta_rechazada(fx):
    html = _registrar(fx, direccion='abc')
    assert 'mínimo 5 caracteres' in html


def test_contrasenas_distintas_rechazadas(fx):
    html = _registrar(fx, confirmar='otra-clave')
    assert 'no coinciden' in html


def test_contrasena_corta_rechazada(fx):
    html = _registrar(fx, contrasena='abc', confirmar='abc')
    assert 'al menos 6 caracteres' in html
