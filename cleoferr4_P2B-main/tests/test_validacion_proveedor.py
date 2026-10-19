"""FASE 2 — validaciones de servidor de los forms de PROVEEDOR.

La razón social ADMITE dígitos ('Ferretería Los 2 Amigos'); el contacto
es nombre de persona (sin dígitos); correo opcional con formato; y las
longitudes siguen los límites reales de las columnas (100/50/20/100).
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
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'c': _admin(), 'cur': cur}
    proxy.rollback()
    raw.close()


def _guardar(fx, **campos):
    datos = {'nombre': 'Ferretería zzqf2prov', 'contacto': 'Juan Pérez',
             'telefono': '987654321', 'email': 'ventas@zzqf2prov.mail.invalid',
             'direccion': 'Av. Industrial 123'}
    datos.update(campos)
    return fx['c'].post('/proveedores/nuevo', data=datos,
                        follow_redirects=True).get_data(as_text=True)


def _existe(fx, nombre):
    fx['cur'].execute("SELECT * FROM proveedor WHERE nombre = %s", (nombre,))
    return fx['cur'].fetchone()


# ── razón social ─────────────────────────────────────────────────

def test_razon_social_con_digitos_aceptada(fx):
    html = _guardar(fx, nombre='Ferretería Los 2 Amigos S.A.C.')
    assert 'Proveedor registrado' in html
    assert _existe(fx, 'Ferretería Los 2 Amigos S.A.C.') is not None


def test_razon_social_solo_espacios_rechazada(fx):
    html = _guardar(fx, nombre='   ')
    assert 'El campo «razón social» es obligatorio' in html


def test_razon_social_excesiva_rechazada(fx):
    html = _guardar(fx, nombre='R' * 101)
    assert 'no puede superar 100 caracteres' in html


# ── contacto (nombre de persona) ────────────────────────────────

def test_contacto_con_digitos_rechazado(fx):
    html = _guardar(fx, contacto='Juan Pérez 123')
    assert 'El campo «contacto» solo admite letras' in html


def test_contacto_con_tildes_aceptado(fx):
    html = _guardar(fx, contacto='Álvarez Ñúñez')
    assert 'Proveedor registrado' in html


def test_contacto_vacio_permitido(fx):
    html = _guardar(fx, contacto='')
    assert 'Proveedor registrado' in html


# ── teléfono, dirección, correo ─────────────────────────────────

def test_telefono_excesivo_rechazado(fx):
    html = _guardar(fx, telefono='9' * 21)
    assert 'no puede superar 20 caracteres' in html


def test_direccion_excesiva_rechazada(fx):
    html = _guardar(fx, direccion='d' * 101)
    assert 'no puede superar 100 caracteres' in html


def test_correo_sin_formato_rechazado(fx):
    html = _guardar(fx, email='no-es-correo')
    assert 'no tiene un formato válido' in html


def test_correo_vacio_permitido(fx):
    html = _guardar(fx, email='')
    assert 'Proveedor registrado' in html
    fila = _existe(fx, 'Ferretería zzqf2prov')
    assert fila['email'] is None


# ── edición ─────────────────────────────────────────────────────

def _proveedor(fx):
    fx['cur'].execute("""
        INSERT INTO proveedor (nombre, contacto, telefono, email, direccion)
        VALUES ('Original zzqf2prov', 'Pedro Soto', '', '', '')
        RETURNING id_proveedor
    """)
    return fx['cur'].fetchone()['id_proveedor']


def test_editar_valida_y_no_toca_la_bd(fx):
    pid = _proveedor(fx)
    html = fx['c'].post(f'/proveedores/editar/{pid}',
                        data={'nombre': '   '},
                        follow_redirects=True).get_data(as_text=True)
    assert 'El campo «razón social» es obligatorio' in html
    fx['cur'].execute("SELECT nombre FROM proveedor WHERE id_proveedor=%s",
                      (pid,))
    assert fx['cur'].fetchone()['nombre'] == 'Original zzqf2prov'


def test_editar_camino_feliz(fx):
    pid = _proveedor(fx)
    html = fx['c'].post(f'/proveedores/editar/{pid}',
                        data={'nombre': 'Editada 2 Hermanos',
                              'contacto': 'Rosa Ñúñez',
                              'telefono': '999888777',
                              'email': 'rosa@zzqf2prov.mail.invalid',
                              'direccion': 'Jr. Unión 45'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'Proveedor actualizado' in html
    fx['cur'].execute("SELECT nombre, contacto, telefono, email, direccion"
                      " FROM proveedor WHERE id_proveedor=%s", (pid,))
    fila = fx['cur'].fetchone()
    assert fila['nombre'] == 'Editada 2 Hermanos'
    assert fila['contacto'] == 'Rosa Ñúñez'
    assert fila['telefono'] == '999888777'
