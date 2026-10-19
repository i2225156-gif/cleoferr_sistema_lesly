"""FASE 2 — validaciones de servidor de los forms de CLIENTE (admin).

Casos límite por campo: nombre de persona con dígitos (rechazado),
tildes/ñ (aceptadas), solo espacios, correo sin formato, duplicado con
mensaje amigable, teléfono/dirección excesivos, contraseña obligatoria
(el default '123456' quedó eliminado) y el camino feliz dentro del
rollback. La edición aplica las mismas reglas sin dejar tocar la BD.
"""
import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app

MARCA = 'zzqf2cli'


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


def _admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_auth.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy)
    yield {'c': _admin(), 'cur': cur}
    proxy.rollback()
    raw.close()


def _guardar(fx, **campos):
    datos = {'nombre': 'Zzqfcli Cliente', 'email': 'zzqf2cli@mail.invalid',
             'telefono': '999888777', 'direccion': 'Av. Prueba 123',
             'contrasena': 'clave-ok-9', 'csrf_token': 'token-test'}
    datos.update(campos)
    return fx['c'].post('/clientes/nuevo', data=datos,
                        follow_redirects=True).get_data(as_text=True)


def _existe(fx, email):
    fx['cur'].execute("SELECT id_cliente FROM cliente WHERE email = %s",
                      (email,))
    return fx['cur'].fetchone()


# ── nombre de persona ───────────────────────────────────────────

def test_nombre_con_digitos_rechazado(fx):
    html = _guardar(fx, nombre='Juan123 Perez')
    assert 'solo admite letras' in html
    assert _existe(fx, 'zzqf2cli@mail.invalid') is None


def test_nombre_solo_espacios_rechazado(fx):
    html = _guardar(fx, nombre='   ')
    assert 'El campo «nombre» es obligatorio' in html


def test_nombre_con_tildes_y_enie_aceptado(fx):
    html = _guardar(fx, nombre='José Ñuñoz Pérez')
    assert 'Cliente registrado correctamente' in html
    fila = _existe(fx, 'zzqf2cli@mail.invalid')
    assert fila is not None
    fx['cur'].execute("SELECT nombre, apellido FROM cliente WHERE id_cliente=%s",
                      (fila['id_cliente'],))
    reg = fx['cur'].fetchone()
    assert reg['nombre'] == 'José' and reg['apellido'] == 'Ñuñoz Pérez'


def test_nombre_excesivo_rechazado(fx):
    html = _guardar(fx, nombre='A' * 101)
    assert 'no puede superar 100 caracteres' in html


# ── correo ───────────────────────────────────────────────────────

def test_correo_sin_formato_rechazado(fx):
    html = _guardar(fx, email='no-es-correo')
    assert 'no tiene un formato válido' in html
    assert _existe(fx, 'no-es-correo') is None


def test_correo_duplicado_mensaje_amigable(fx):
    assert 'Cliente registrado correctamente' in _guardar(fx)
    html = _guardar(fx, email='zzqf2cli@mail.invalid')
    assert 'Ese correo ya está registrado por otro cliente' in html
    fx['cur'].execute("SELECT COUNT(*) AS n FROM cliente WHERE email=%s",
                      ('zzqf2cli@mail.invalid',))
    assert fx['cur'].fetchone()['n'] == 1


# ── teléfono, dirección y contraseña ────────────────────────────

def test_telefono_excesivo_rechazado(fx):
    html = _guardar(fx, telefono='9' * 21)
    assert 'no puede superar 20 caracteres' in html


def test_direccion_excesiva_rechazada(fx):
    html = _guardar(fx, direccion='d' * 201)
    assert 'no puede superar 200 caracteres' in html


def test_contrasena_obligatoria_sin_default(fx):
    """El default '123456' quedó eliminado: sin contraseña no se guarda."""
    html = _guardar(fx, contrasena='')
    assert 'al menos 6 caracteres' in html
    assert _existe(fx, 'zzqf2cli@mail.invalid') is None


def test_contrasena_corta_rechazada(fx):
    html = _guardar(fx, contrasena='123')
    assert 'al menos 6 caracteres' in html


# ── edición: mismas reglas, sin tocar la BD si falla ────────────

def _cliente_sintetico(fx):
    fx['cur'].execute("""
        INSERT INTO cliente (nombre, apellido, email, telefono, direccion,
                             contrasena, activo, id_tipo_documento, origen)
        VALUES ('Original', 'Prueba', 'edita-zzqf2cli@mail.invalid', '', '',
                'x-hash', TRUE, 1, 'pos')
        RETURNING id_cliente
    """)
    return fx['cur'].fetchone()['id_cliente']


def test_editar_rechaza_digitos_y_no_toca_la_bd(fx):
    cid = _cliente_sintetico(fx)
    html = fx['c'].post(f'/clientes/editar/{cid}',
                        data={'nombre': 'Editado 42', 'email': 'edita-zzqf2cli@mail.invalid'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'solo admite letras' in html
    fx['cur'].execute("SELECT nombre FROM cliente WHERE id_cliente=%s", (cid,))
    assert fx['cur'].fetchone()['nombre'] == 'Original'


def test_editar_correo_ajeno_rechazado(fx):
    cid = _cliente_sintetico(fx)
    # otro cliente con ese correo
    fx['cur'].execute("""
        INSERT INTO cliente (nombre, apellido, email, contrasena, activo,
                             id_tipo_documento, origen)
        VALUES ('Otro', 'Cliente', 'otro-zzqf2cli@mail.invalid', 'x', TRUE, 1, 'pos')
    """)
    html = fx['c'].post(f'/clientes/editar/{cid}',
                        data={'nombre': 'Edición Válida',
                              'email': 'otro-zzqf2cli@mail.invalid'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'Ese correo ya está registrado por otro cliente' in html


def test_editar_camino_feliz_con_tildes(fx):
    cid = _cliente_sintetico(fx)
    html = fx['c'].post(f'/clientes/editar/{cid}',
                        data={'nombre': 'María Ñusta', 'email': 'edita-zzqf2cli@mail.invalid',
                              'telefono': '987654321', 'direccion': 'Jr. Las Ñustas 45'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'Cliente actualizado correctamente' in html
    fx['cur'].execute("SELECT nombre, telefono, direccion"
                      " FROM cliente WHERE id_cliente=%s", (cid,))
    fila = fx['cur'].fetchone()
    # La edición NO divide nombre/apellido (comportamiento histórico): el
    # nombre completo va a la columna nombre, igual que siempre.
    assert fila['nombre'] == 'María Ñusta'
    assert fila['telefono'] == '987654321'
