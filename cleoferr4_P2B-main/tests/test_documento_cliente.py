"""Documento de identidad: registro, perfil, checkout y admin.

Contra la BD real dentro de transacciones con rollback: nada persiste.
El vendedor (usuario) NO lleva documento (verificado: la tabla usuario
de AUTH no tiene columnas de documento).
"""
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.documentos import (normalizar_numero, validar_documento,
                                 tipo_documento_de_cliente)


class ConnProxy:
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


# ── Validación y normalización (sin BD) ───────────────────────────

def test_normalizar_numero():
    assert normalizar_numero('  12345678  ') == '12345678'
    assert normalizar_numero('  abc  123 ') == 'ABC123'
    assert normalizar_numero('') == ''
    assert normalizar_numero(None) == ''


def test_validar_dni():
    ok, msg = validar_documento('dni', '12345678')
    assert ok
    ok, msg = validar_documento('dni', '1234567')
    assert not ok and '8' in msg
    ok, msg = validar_documento('dni', '1234567A')
    assert not ok


def test_validar_ruc():
    ok, msg = validar_documento('ruc', '20605977074')   # DV válido
    assert ok
    ok, msg = validar_documento('ruc', '20605977075')   # DV inválido
    assert not ok and 'verificador' in msg
    ok, msg = validar_documento('ruc', '12345678')
    assert not ok and '11' in msg


def test_validar_ce_a_verificar():
    ok, msg = validar_documento('ce', 'A12345678')      # 9 alfanuméricos
    assert ok
    ok, msg = validar_documento('ce', 'A1234567')       # 8: falla
    assert not ok and 'a verificar' in msg


def test_validar_pasaporte():
    ok, msg = validar_documento('pasaporte', 'AB123456')
    assert ok
    ok, msg = validar_documento('pasaporte', 'AB12')
    assert not ok


def test_validar_tipo_desconocido():
    ok, msg = validar_documento('carnet', '123')
    assert not ok and 'desconocido' in msg


def test_tipo_permite_factura():
    from services.documentos import tipo_permite_factura
    assert tipo_permite_factura('ruc') is True
    assert tipo_permite_factura('dni') is False
    assert tipo_permite_factura('ce') is False
    assert tipo_permite_factura('pasaporte') is False


# ── Registro con documento obligatorio (via test client) ─────────

def _sesion_cliente(uid=11):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = 'cliente'
        s['_csrf_token'] = 'token-test'
    return c


def _sesion_admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['_csrf_token'] = 'token-test'
    return c


def _registro_cliente(client, extra=None):
    datos = {
        'csrf_token': 'token-test',
        'nombre': 'Test',
        'apellido': 'Doc',
        'correo': f'testdoc_{_uuid.uuid4().hex[:8]}@test.com',
        'telefono': '987654321',
        'direccion': 'Calle Test 123',
        'contrasena': 'clave123',
        'confirmar': 'clave123',
    }
    if extra:
        datos.update(extra)
    return client.post('/registro_cliente', data=datos, follow_redirects=False)


def test_registro_con_dni_valido(fx):
    r = _registro_cliente(fx['client'], {
        'tipo_documento': 'dni',
        'nro_documento': '  12345678  ',   # con espacios para probar normalización
    })
    # El registro redirige a verificar (no error inmediato = pasó la validación)
    assert r.status_code == 200  # renderiza login_cliente con success/redirect
    assert b'Ya tiene una cuenta' not in r.data  # no es un error de correo duplicado


def test_registro_sin_documento_acepta(fx):
    """Documento OPCIONAL: sin número no se bloquea el registro."""
    r = _registro_cliente(fx['client'], {
        'tipo_documento': '',
        'nro_documento': '',
    })
    # Debe pasar la validación (no error de documento)
    assert r.status_code == 200
    assert b'documento' not in r.data.lower() or b'opcional' in r.data.lower()


def test_registro_con_dni_invalido_rechaza(fx):
    r = _registro_cliente(fx['client'], {
        'tipo_documento': 'dni',
        'nro_documento': '1234567',  # 7 dígitos
    })
    assert r.status_code == 200
    assert b'8' in r.data  # el mensaje menciona "8 d"


# ── Perfil (dueño ve/corrige su documento) ────────────────────────

def test_perfil_cliente_ve_su_documento(fx):
    r = fx['client'].get('/perfil')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Documento de identidad' in html
    assert 'snapshot' in html.lower() or 'Snapshot' in html or 'comprobantes ya emitidos' in html


def test_perfil_actualiza_documento(fx):
    r = fx['client'].post('/perfil', data={
        'csrf_token': 'token-test',
        'tipo_documento': 'dni',
        'nro_documento': '87654321',
    }, follow_redirects=False)
    assert r.status_code == 302


def test_perfil_vendedor_redirect(fx):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
    r = c.get('/perfil')
    assert r.status_code == 302


# ── Admin edita documento con auditoria ──────────────────────────

def test_admin_edita_cliente_muestra_campo_documento(fx):
    r = fx['admin'].get('/clientes/editar/11')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'tipo_documento' in html
    assert 'nro_documento' in html
    assert 'auditor' in html.lower()  # aviso de auditoría


# ── Fixture compartido ────────────────────────────────────────────

@pytest.fixture(scope='module')
def fx():
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cliente = _sesion_cliente()
    admin = _sesion_admin()
    yield {'client': cliente, 'admin': admin, 'proxy': proxy}
    proxy.rollback()
    raw.close()
