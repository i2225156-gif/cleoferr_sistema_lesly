"""Documento de identidad del proveedor: validación y persistencia.

- services.validacion.documento_proveedor: RUC (11 dígitos, prefijo y
  dígito verificador módulo 11 via ruc_valido_para_factura), DNI (exacto
  8) y CE (9-12 alfanumérico); espacios se quitan; vacío se rechaza.
- Rutas nuevo_proveedor / editar_proveedor: INSERT y UPDATE incluyen el
  documento, duplicado (UniqueViolation) da mensaje claro con flash y la
  migración no aplicada (UndefinedColumn) guarda sin el documento.

Sin BD: se monkeyparchea app.get_connection_tienda con una conexión falsa.
"""
from dotenv import load_dotenv

load_dotenv()

import pytest
from psycopg2 import errors as pg_errors

import app as modulo_app
from services.validacion import documento_proveedor

RUC_VALIDO = '20123456786'      # módulo 11 válido (prefijo 20)
RUC_DG_MAL = '20123456787'      # mismo RUC con dígito verificador falso
CE_OK = 'A1234567Z'             # 9 caracteres alfanuméricos


# ── Validación de servidor ──────────────────────────────────
def test_ruc_valido_y_quita_espacios():
    tipo, numero, error = documento_proveedor(' RUC ', f' {RUC_VALIDO[:8]} {RUC_VALIDO[8:]} ')
    assert error is None
    assert tipo == 'RUC'
    assert numero == RUC_VALIDO


def test_ruc_longitud_invalida():
    tipo, numero, error = documento_proveedor('RUC', '1234567')
    assert tipo is None and numero is None
    assert '11 dígitos' in error


def test_ruc_prefijo_invalido():
    tipo, numero, error = documento_proveedor('RUC', '99123456789')
    assert tipo is None and numero is None
    assert '10, 15, 17 o 20' in error


def test_ruc_digito_verificador_invalido():
    tipo, numero, error = documento_proveedor('RUC', RUC_DG_MAL)
    assert tipo is None and numero is None
    assert 'dígito verificador' in error


def test_ruc_prefijo_15_digito_valido():
    tipo, numero, error = documento_proveedor('RUC', '15012345671')
    assert error is None
    assert (tipo, numero) == ('RUC', '15012345671')


def test_dni_valido_e_invalido():
    tipo, numero, error = documento_proveedor('DNI', ' 12345678 ')
    assert error is None
    assert (tipo, numero) == ('DNI', '12345678')
    tipo, numero, error = documento_proveedor('DNI', '1234567')
    assert tipo is None and numero is None
    assert '8 dígitos' in error


def test_ce_valido_invalido_y_mayusculas():
    tipo, numero, error = documento_proveedor('CE', ' a1234567z ')
    assert error is None
    assert (tipo, numero) == ('CE', 'A1234567Z')
    tipo, numero, error = documento_proveedor('CE', 'A123%')       # 5, con símbolo
    assert tipo is None and numero is None
    assert '9 y 12' in error


def test_tipo_invalido():
    tipo, numero, error = documento_proveedor('Pasaporte', 'A12345678')
    assert tipo is None and numero is None
    assert 'RUC, DNI o CE' in error


def test_documento_vacio_rechazo():
    for tipo in ('RUC', 'DNI', 'CE'):
        tipo_n, numero_n, error = documento_proveedor(tipo, '   ')
        assert tipo_n is None and numero_n is None
        assert error == 'El número de documento es obligatorio.'


# ── Rutas (conexión falsa, sin BD real) ─────────────────────
class FakeCursor:
    def __init__(self, grabador):
        self._grabador = grabador

    def execute(self, sql, params=None):
        self._grabador['llamadas'].append((sql, tuple(params or ())))
        if self._grabador['excepcion'] is not None:
            exc = self._grabador['excepcion']
            self._grabador['excepcion'] = None   # solo una vez
            raise exc

    def fetchone(self):
        return None


class FakeConn:
    def __init__(self, grabador):
        self._grabador = grabador

    def cursor(self, dictionary=False):
        return FakeCursor(self._grabador)

    def commit(self):
        self._grabador['commits'] += 1

    def rollback(self):
        self._grabador['rollbacks'] += 1

    def close(self):
        self._grabador['cerrada'] += 1


@pytest.fixture
def cliente_proveedor(monkeypatch):
    """test_client con sesión de admin y get_connection_tienda falsa."""
    grabador = {'llamadas': [], 'excepcion': None,
                'commits': 0, 'rollbacks': 0, 'cerrada': 0}
    monkeypatch.setattr(modulo_app, 'get_connection_tienda',
                        lambda: FakeConn(grabador))
    with modulo_app.app.test_client() as c:
        with c.session_transaction() as s:
            s['usuario_id'] = 1
            s['rol'] = 'administrador'
            s['nombre'] = 'Test Admin'
        yield c, grabador


def _mensajes(cliente):
    with cliente.session_transaction() as s:
        return [m for (_cat, m) in s.get('_flashes', [])]


def test_insert_incluye_documento(cliente_proveedor):
    c, grab = cliente_proveedor
    resp = c.post('/proveedores/nuevo', data={
        'nombre': 'Proveedor Prueba S.A.C.',
        'tipo_documento': 'RUC',
        'numero_documento': RUC_VALIDO,
    })
    assert resp.status_code == 302
    sql, params = grab['llamadas'][0]
    assert 'INSERT INTO proveedor' in sql
    assert 'tipo_documento' in sql and 'numero_documento' in sql
    assert params[-2:] == ('RUC', RUC_VALIDO)
    assert 'Proveedor registrado.' in _mensajes(c)


def test_update_incluye_documento(cliente_proveedor):
    c, grab = cliente_proveedor
    resp = c.post('/proveedores/editar/7', data={
        'nombre': 'Proveedor Editado S.A.C.',
        'tipo_documento': 'DNI',
        'numero_documento': '12345678',
    })
    assert resp.status_code == 302
    sql, params = grab['llamadas'][0]
    assert 'UPDATE proveedor' in sql
    assert 'tipo_documento=%s' in sql and 'numero_documento=%s' in sql
    assert params[-3:] == ('DNI', '12345678', 7)
    assert 'Proveedor actualizado.' in _mensajes(c)


def test_duplicado_nuevo_mensaje_claro(cliente_proveedor):
    c, grab = cliente_proveedor
    grab['excepcion'] = pg_errors.UniqueViolation()
    resp = c.post('/proveedores/nuevo', data={
        'nombre': 'Duplicado S.A.C.',
        'tipo_documento': 'RUC',
        'numero_documento': RUC_VALIDO,
    })
    assert resp.status_code == 302
    assert 'Ya existe un proveedor con ese documento.' in _mensajes(c)
    assert grab['rollbacks'] >= 1


def test_duplicado_editar_mensaje_claro(cliente_proveedor):
    c, grab = cliente_proveedor
    grab['excepcion'] = pg_errors.UniqueViolation()
    resp = c.post('/proveedores/editar/7', data={
        'nombre': 'Duplicado Edicion S.A.C.',
        'tipo_documento': 'RUC',
        'numero_documento': RUC_VALIDO,
    })
    assert resp.status_code == 302
    assert 'Ya existe un proveedor con ese documento.' in _mensajes(c)


def test_migracion_no_aplicada_guarda_sin_documento(cliente_proveedor):
    c, grab = cliente_proveedor
    grab['excepcion'] = pg_errors.UndefinedColumn()
    resp = c.post('/proveedores/nuevo', data={
        'nombre': 'Sin Migracion S.A.C.',
        'tipo_documento': 'RUC',
        'numero_documento': RUC_VALIDO,
    })
    assert resp.status_code == 302
    assert len(grab['llamadas']) == 2          # intento + fallback
    sql_fallback, params_fallback = grab['llamadas'][1]
    assert 'tipo_documento' not in sql_fallback
    assert 'numero_documento' not in sql_fallback
    assert params_fallback == ('Sin Migracion S.A.C.', None, None, None, None)
    assert 'Proveedor registrado.' in _mensajes(c)


def test_cliente_vacio_documento_rechazo_sin_insert(cliente_proveedor):
    c, grab = cliente_proveedor
    resp = c.post('/proveedores/nuevo', data={
        'nombre': 'Sin Documento S.A.C.',
        'tipo_documento': 'RUC',
        'numero_documento': '   ',
    })
    assert resp.status_code == 302
    assert grab['llamadas'] == []              # no llegó a la BD
    assert 'El número de documento es obligatorio.' in _mensajes(c)
