"""FASE 2 — validaciones de servidor del form de PRODUCTO (por campo).

Cubre los casos límite del jurado: solo espacios, vacío/nulo, negativos,
no numéricos, dígitos y tildes/ñ en el nombre, longitudes excesivas, FKs
inexistentes y el camino FELIZ (que debe persistir dentro del rollback).

Las rutas de producto se convirtieron a conexión cruda proxied
(get_connection_tienda) para poder probar con rollback — antes usaban
db.session de SQLAlchemy y no se podían testear sin persistir (el
precedente de test_productos_eliminar dejaba el alta/edición 'cubierta
por revisión'). También cubre las páginas 404/500 sin detalles técnicos.
"""
import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app, _error_500


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
    datos = {'nombre': 'Testf2 Producto', 'precio': '10.50', 'stock': '5',
             'descripcion': 'Una descripción', 'id_categoria': '',
             'id_marca': '', 'estado': 'activo'}
    datos.update(campos)
    return fx['c'].post('/productos/guardar', data=datos,
                        follow_redirects=True).get_data(as_text=True)


def _persistido(fx, nombre):
    fx['cur'].execute("SELECT nombre FROM producto WHERE nombre = %s",
                      (nombre,))
    return fx['cur'].fetchone()


# ── nombre ──────────────────────────────────────────────────────

def test_nombre_solo_espacios_rechazado(fx):
    html = _guardar(fx, nombre='   ')
    assert 'El campo «nombre» es obligatorio' in html
    assert _persistido(fx, '   ') is None


def test_nombre_ausente_rechazado_sin_500(fx):
    r = fx['c'].post('/productos/guardar', data={'precio': '1', 'stock': '1'},
                     follow_redirects=True)
    assert r.status_code == 200
    assert 'El campo «nombre» es obligatorio' in r.get_data(as_text=True)


def test_nombre_excesivo_rechazado(fx):
    html = _guardar(fx, nombre='x' * 151)
    assert 'no puede superar 150 caracteres' in html
    assert _persistido(fx, 'x' * 151) is None


def test_nombre_con_digitos_y_tildes_aceptado(fx):
    """El nombre de PRODUCTO sí admite dígitos y tildes/ñ."""
    html = _guardar(fx, nombre='Taladro Percútor Ñoño 650W')
    assert 'Producto creado correctamente' in html
    fila = _persistido(fx, 'Taladro Percútor Ñoño 650W')
    assert fila is not None


# ── precio y stock ──────────────────────────────────────────────

def test_precio_no_numerico_rechazado(fx):
    html = _guardar(fx, precio='abc')
    assert 'El campo «precio» debe ser un número' in html
    assert _persistido(fx, 'Testf2 Producto') is None


def test_precio_negativo_rechazado(fx):
    html = _guardar(fx, precio='-5')
    assert 'El campo «precio» no puede ser negativo' in html


def test_precio_cero_rechazado(fx):
    """Decisión del propietario: el precio debe ser mayor que cero."""
    html = _guardar(fx, precio='0')
    assert 'El campo «precio» debe ser mayor que cero' in html
    assert _persistido(fx, 'Testf2 Producto') is None


def test_stock_no_entero_rechazado(fx):
    html = _guardar(fx, stock='2.5')
    assert 'El campo «stock» debe ser un número entero' in html


def test_stock_negativo_rechazado(fx):
    html = _guardar(fx, stock='-3')
    assert 'El campo «stock» no puede ser negativo' in html


def test_stock_texto_rechazado(fx):
    html = _guardar(fx, stock='abc')
    assert 'El campo «stock» debe ser un número entero' in html


# ── descripción y estado ────────────────────────────────────────

def test_descripcion_solo_espacios_queda_vacia(fx):
    """La descripción es opcional: solo espacios se trimea a NULL."""
    html = _guardar(fx, nombre='Testf2 DescVacia', descripcion='   ')
    assert 'Producto creado correctamente' in html
    fx['cur'].execute("SELECT descripcion FROM producto WHERE nombre = %s",
                      ('Testf2 DescVacia',))
    assert fx['cur'].fetchone()['descripcion'] is None


def test_descripcion_excesiva_rechazada(fx):
    html = _guardar(fx, descripcion='y' * 2001)
    assert 'no puede superar 2000 caracteres' in html


def test_estado_invalido_cae_en_activo(fx):
    html = _guardar(fx, nombre='Testf2 Estado', estado='cualquier-cosa')
    assert 'Producto creado correctamente' in html
    fx['cur'].execute("SELECT activo FROM producto WHERE nombre = %s",
                      ('Testf2 Estado',))
    assert fx['cur'].fetchone()['activo'] is True


# ── FKs de categoría y marca ─────────────────────────────────────

def test_categoria_inexistente_rechazada(fx):
    html = _guardar(fx, id_categoria='999999')
    assert 'La categoría seleccionada no existe' in html


def test_categoria_no_numerica_rechazada(fx):
    html = _guardar(fx, id_categoria='abc')
    assert 'La categoría seleccionada no es válida' in html


def test_marca_inexistente_rechazada(fx):
    html = _guardar(fx, id_marca='999999')
    assert 'La marca seleccionada no existe' in html


# ── edición (misma validación) ──────────────────────────────────

def test_actualizar_valida_igual_y_no_toca_la_bd(fx):
    """Con datos inválidos la edición rechaza y el producto queda igual."""
    fx['cur'].execute("""
        INSERT INTO producto (nombre, precio, stock)
        VALUES ('Testf2 Original', 9.99, 3) RETURNING id_producto
    """)
    pid = fx['cur'].fetchone()['id_producto']
    html = fx['c'].post(f'/productos/actualizar/{pid}',
                        data={'nombre': '   ', 'precio': '1', 'stock': '1'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'El campo «nombre» es obligatorio' in html
    fx['cur'].execute("SELECT nombre FROM producto WHERE id_producto = %s",
                      (pid,))
    assert fx['cur'].fetchone()['nombre'] == 'Testf2 Original'


def test_actualizar_camino_feliz(fx):
    fx['cur'].execute("""
        INSERT INTO producto (nombre, precio, stock)
        VALUES ('Testf2 Original', 9.99, 3) RETURNING id_producto
    """)
    pid = fx['cur'].fetchone()['id_producto']
    html = fx['c'].post(f'/productos/actualizar/{pid}',
                        data={'nombre': 'Testf2 Editado Ñoño', 'precio': '12',
                              'stock': '7', 'descripcion': '', 'estado': 'inactivo'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'Producto actualizado correctamente' in html
    fx['cur'].execute("SELECT nombre, precio, stock, activo FROM producto"
                      " WHERE id_producto = %s", (pid,))
    fila = fx['cur'].fetchone()
    assert fila['nombre'] == 'Testf2 Editado Ñoño'
    assert float(fila['precio']) == 12
    assert fila['stock'] == 7
    assert fila['activo'] is False


def test_actualizar_inexistente_da_404(fx):
    r = fx['c'].post('/productos/actualizar/999999',
                     data={'nombre': 'x', 'precio': '1', 'stock': '1'})
    assert r.status_code == 404


# ── páginas de error sin detalles técnicos ───────────────────────

def test_404_amigable_sin_detalles():
    r = app.test_client().get('/ruta-que-no-existe-xyz')
    assert r.status_code == 404
    html = r.get_data(as_text=True)
    assert 'no existe o fue movida' in html
    assert 'Traceback' not in html


def test_500_amigable_sin_detalles():
    with app.test_request_context('/'):
        respuesta = _error_500(Exception('detalle técnico secreto'))
    html = str(respuesta)
    assert 'error inesperado' in html
    assert 'detalle técnico secreto' not in html
    assert 'Traceback' not in html
