"""guardar_imagen: Storage, validación real del archivo y fallback local.

Sin red real: ``almacenamiento.subir`` se simula. Las altas/ediciones usan
una conexión proxied con rollback para no persistir datos sintéticos.
"""
import io

import psycopg2
import psycopg2.extras
import pytest

import app as modulo_app
import db_supabase
from app import app, url_imagen

PNG = b'\x89PNG\r\n\x1a\n' + b'imagen sintetica'
URL_REMOTA = 'https://proyecto.supabase.co/storage/v1/object/public/productos/foto.png'


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
    yield {'c': _vendedor(), 'cur': cur, 'monkeypatch': monkeypatch}
    proxy.rollback()
    raw.close()


def _configurar_storage(monkeypatch):
    monkeypatch.setenv('SUPABASE_TIENDA_PROJECT_URL',
                       'https://proyecto.supabase.co')
    monkeypatch.setenv('SUPABASE_TIENDA_SECRET_KEY', 'clave-de-prueba')
    monkeypatch.setenv('SUPABASE_BUCKET_PRODUCTOS', 'productos')


def _guardar(fx, nombre, contenido=PNG, archivo='producto.png'):
    return fx['c'].post('/productos/guardar', data={
        'nombre': nombre, 'precio': '10.50', 'stock': '5',
        'descripcion': '', 'estado': 'activo',
        'imagen': (io.BytesIO(contenido), archivo),
    }, follow_redirects=True).get_data(as_text=True)


def _imagen(fx, nombre):
    fx['cur'].execute("SELECT imagen FROM producto WHERE nombre = %s",
                      (nombre,))
    return fx['cur'].fetchone()['imagen']


def test_storage_configurado_guarda_la_url_publica(fx):
    _configurar_storage(fx['monkeypatch'])
    visto = {}

    def subir_falso(contenido, nombre, tipo_mime):
        visto.update(contenido=contenido, nombre=nombre, tipo_mime=tipo_mime)
        return URL_REMOTA

    fx['monkeypatch'].setattr(modulo_app.almacenamiento, 'subir', subir_falso)

    html = _guardar(fx, 'Testimg Storage ok')

    assert 'Producto creado correctamente' in html
    assert _imagen(fx, 'Testimg Storage ok') == URL_REMOTA
    assert visto['contenido'].startswith(b'\x89PNG\r\n\x1a\n')
    assert visto['nombre'].endswith('.png')
    assert visto['tipo_mime'] == 'image/png'


def test_fallo_de_red_guarda_sin_imagen_y_avisa(fx):
    _configurar_storage(fx['monkeypatch'])
    fx['monkeypatch'].setattr(modulo_app.almacenamiento, 'subir',
                              lambda *a, **k: None)

    html = _guardar(fx, 'Testimg Storage caido')

    assert 'El producto se guardará sin imagen nueva' in html
    assert _imagen(fx, 'Testimg Storage caido') is None


def test_tipo_falso_con_extension_jpg_se_rechaza(fx):
    html = _guardar(fx, 'Testimg Tipo falso', contenido=b'no soy una imagen',
                    archivo='producto.jpg')

    assert 'Formato no permitido' in html
    assert _imagen(fx, 'Testimg Tipo falso') is None


def test_archivo_mayor_a_4_mb_se_rechaza(fx):
    grande = b'\x89PNG\r\n\x1a\n' + b'0' * (4 * 1024 * 1024)
    html = _guardar(fx, 'Testimg Muy grande', contenido=grande)

    assert '4 MB' in html
    assert _imagen(fx, 'Testimg Muy grande') is None


def test_editar_con_imagen_remota_no_borra_ni_falla(fx):
    _configurar_storage(fx['monkeypatch'])
    fx['cur'].execute("""
        INSERT INTO producto (nombre, precio, stock, imagen)
        VALUES ('Testimg Remota original', 9.99, 3, %s)
        RETURNING id_producto
    """, (URL_REMOTA,))
    pid = fx['cur'].fetchone()['id_producto']

    def no_borrar(*args, **kwargs):
        raise AssertionError('no debe borrarse una imagen remota')

    fx['monkeypatch'].setattr(modulo_app.os, 'remove', no_borrar)
    fx['monkeypatch'].setattr(modulo_app.almacenamiento, 'subir',
                              lambda *a, **k: None)

    html = fx['c'].post(f'/productos/actualizar/{pid}', data={
        'nombre': 'Testimg Remota editada', 'precio': '12', 'stock': '7',
        'descripcion': '', 'estado': 'activo',
        'imagen': (io.BytesIO(PNG), 'nueva.png'),
    }, follow_redirects=True).get_data(as_text=True)

    assert 'Producto actualizado correctamente' in html
    fx['cur'].execute(
        "SELECT nombre, imagen FROM producto WHERE id_producto = %s", (pid,))
    fila = fx['cur'].fetchone()
    assert fila['nombre'] == 'Testimg Remota editada'
    assert fila['imagen'] == URL_REMOTA

# ── url_imagen y su uso en catálogo / listado ─────────────────────

def test_url_imagen_resuelve_remota_y_local():
    with app.test_request_context('/'):
        assert url_imagen('https://proyecto.supabase.co/foto.png') == \
            'https://proyecto.supabase.co/foto.png'
        assert url_imagen('http://ejemplo.test/foto.png') == \
            'http://ejemplo.test/foto.png'
        assert url_imagen('casco.jpg') == '/static/img/casco.jpg'
        assert url_imagen('') == ''
    assert app.jinja_env.filters['url_imagen'] is url_imagen


def _insertar_producto(fx, nombre, imagen):
    fx['cur'].execute("""
        INSERT INTO producto (nombre, precio, stock, imagen, activo)
        VALUES (%s, 10.50, 5, %s, TRUE)
        RETURNING id_producto
    """, (nombre, imagen))
    return fx['cur'].fetchone()['id_producto']


def _listado(fx):
    # El producto sintético es el de id más alto: aparece en la última página.
    return fx['c'].get('/productos?pagina=999999').get_data(as_text=True)


def test_catalogo_y_lista_muestran_la_url_remota(fx):
    _insertar_producto(fx, 'Testurl Remota', URL_REMOTA)

    catalogo = fx['c'].get('/catalogo').get_data(as_text=True)
    productos = _listado(fx)

    assert URL_REMOTA in catalogo
    assert URL_REMOTA in productos


def test_imagen_existente_por_nombre_sigue_resolviendose(fx):
    _insertar_producto(fx, 'Testurl Local', 'casco.jpg')

    catalogo = fx['c'].get('/catalogo').get_data(as_text=True)
    productos = _listado(fx)

    assert '/static/img/casco.jpg' in catalogo
    assert '/static/img/casco.jpg' in productos


def test_valor_malicioso_no_se_inyecta_como_html(fx):
    payload = '"><script>alert(1)</script>'
    _insertar_producto(fx, 'Testurl Maliciosa', payload)

    for html in (fx['c'].get('/catalogo').get_data(as_text=True),
                 _listado(fx)):
        assert '<script>alert(1)</script>' not in html
        # La URL se codifica/escapa: la etiqueta nunca queda como HTML vivo.
        assert ('&lt;script&gt;' in html
                or '%3cscript%3e' in html.lower())


def test_producto_sin_imagen_mantiene_el_placeholder(fx):
    _insertar_producto(fx, 'Testurl Sin imagen', None)

    catalogo = fx['c'].get('/catalogo').get_data(as_text=True)
    productos = _listado(fx)

    assert 'data-imagen=""' in catalogo
    assert 'fa fa-wrench' in catalogo
    assert 'producto-img-placeholder' in productos
