"""TAREA 3.1 — formulario y guardado de fecha_vencimiento y precio_costo.

Ambos campos OPCIONALES (nullable en la BD): sin fecha = producto sin
control de vencimiento; sin costo = el KPI de margen mostrará cobertura.
Fecha inválida y costo negativo/no numérico se rechazan con mensaje
claro (el CHECK producto_precio_costo_check de la BD es la última
barrera). El camino feliz persiste dentro del rollback.
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


def _vendedor():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'c': _vendedor(), 'cur': cur}
    proxy.rollback()
    raw.close()


def _guardar(fx, **campos):
    datos = {'nombre': 'zzqvence Producto', 'precio': '10.50', 'stock': '5',
             'descripcion': '', 'id_categoria': '', 'id_marca': '',
             'estado': 'activo', 'fecha_vencimiento': '', 'precio_costo': ''}
    datos.update(campos)
    return fx['c'].post('/productos/guardar', data=datos,
                        follow_redirects=True).get_data(as_text=True)


def _fila(fx, nombre='zzqvence Producto'):
    fx['cur'].execute("SELECT fecha_vencimiento, precio_costo FROM producto"
                      " WHERE nombre = %s", (nombre,))
    return fx['cur'].fetchone()


# ── camino feliz ────────────────────────────────────────────────

def test_guardar_con_fecha_y_costo(fx):
    html = _guardar(fx, fecha_vencimiento='2026-12-31', precio_costo='4.50')
    assert 'Producto creado correctamente' in html
    fila = _fila(fx)
    assert str(fila['fecha_vencimiento']) == '2026-12-31'
    assert float(fila['precio_costo']) == 4.50


def test_guardar_sin_ambos_queda_null(fx):
    _guardar(fx)
    fila = _fila(fx)
    assert fila['fecha_vencimiento'] is None
    assert fila['precio_costo'] is None


# ── validaciones ────────────────────────────────────────────────

def test_fecha_invalida_rechazada(fx):
    html = _guardar(fx, fecha_vencimiento='31-12-2026')
    assert 'La fecha de vencimiento no es válida' in html
    assert _fila(fx) is None


def test_costo_negativo_rechazado(fx):
    html = _guardar(fx, precio_costo='-1')
    assert 'El campo «precio de costo» no puede ser negativo' in html
    assert _fila(fx) is None


def test_costo_no_numerico_rechazado(fx):
    html = _guardar(fx, precio_costo='abc')
    assert 'El campo «precio de costo» debe ser un número' in html
    assert _fila(fx) is None


def test_costo_solo_espacios_queda_null(fx):
    html = _guardar(fx, precio_costo='   ')
    assert 'Producto creado correctamente' in html
    assert _fila(fx)['precio_costo'] is None


# ── edición ──────────────────────────────────────────────────────

def test_actualizar_cambia_y_limpia_los_campos(fx):
    fx['cur'].execute("""
        INSERT INTO producto (nombre, precio, stock, fecha_vencimiento, precio_costo)
        VALUES ('zzqvence Edit', 9.99, 3, '2026-12-31', 4.50)
        RETURNING id_producto
    """)
    pid = fx['cur'].fetchone()['id_producto']
    html = fx['c'].post(f'/productos/actualizar/{pid}',
                        data={'nombre': 'zzqvence Edit', 'precio': '12',
                              'stock': '7', 'fecha_vencimiento': '2027-03-15',
                              'precio_costo': '5.25'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'Producto actualizado correctamente' in html
    fx['cur'].execute("SELECT fecha_vencimiento, precio_costo FROM producto"
                      " WHERE id_producto = %s", (pid,))
    fila = fx['cur'].fetchone()
    assert str(fila['fecha_vencimiento']) == '2027-03-15'
    assert float(fila['precio_costo']) == 5.25
    # y con ambos vacíos se limpian
    html = fx['c'].post(f'/productos/actualizar/{pid}',
                        data={'nombre': 'zzqvence Edit', 'precio': '12',
                              'stock': '7', 'fecha_vencimiento': '',
                              'precio_costo': ''},
                        follow_redirects=True).get_data(as_text=True)
    assert 'Producto actualizado correctamente' in html
    fx['cur'].execute("SELECT fecha_vencimiento, precio_costo FROM producto"
                      " WHERE id_producto = %s", (pid,))
    fila = fx['cur'].fetchone()
    assert fila['fecha_vencimiento'] is None
    assert fila['precio_costo'] is None
