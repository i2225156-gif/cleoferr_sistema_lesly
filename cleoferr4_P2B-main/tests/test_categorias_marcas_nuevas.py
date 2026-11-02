"""Alta rápida de categorías y marcas desde el formulario de producto.

Toda escritura usa una conexión sintética proxied: commit no sale de la
transacción y el fixture termina siempre con rollback.
"""
import uuid

import psycopg2
import psycopg2.extras
import pytest

import app as modulo_app
import db_supabase
from app import app


TOKEN = 'token-categoria-marca'
RUTAS = (
    ('/productos/categorias/nueva', 'categoria', 'id_categoria'),
    ('/productos/marcas/nueva', 'marca', 'id_marca'),
)


class ConnProxy:
    def __init__(self, raw):
        self._raw = raw

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            return self._raw.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor)
        return self._raw.cursor()

    def commit(self):
        pass

    def rollback(self):
        self._raw.rollback()

    def close(self):
        pass


def _sesion():
    cliente = app.test_client()
    with cliente.session_transaction() as sesion:
        sesion['usuario_id'] = 7001
        sesion['rol'] = 'vendedor'
        sesion['nombre'] = 'Prueba sintética'
        sesion['_csrf_token'] = TOKEN
    return cliente


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    monkeypatch.setitem(
        app.before_request_funcs, None,
        [f for f in app.before_request_funcs[None]
         if f is not modulo_app._cargar_permisos_personal])
    monkeypatch.setattr(modulo_app, '_permisos_staff',
                        lambda: {'productos'})
    yield {'cliente': _sesion(), 'cur': proxy.cursor(dictionary=True)}
    proxy.rollback()
    raw.close()


def _post(fx, ruta, nombre, csrf=True, ajax=True):
    headers = {'Accept': 'application/json' if ajax else 'text/html'}
    if ajax:
        headers['X-Requested-With'] = 'XMLHttpRequest'
    if csrf:
        headers['X-CSRFToken'] = TOKEN
    return fx['cliente'].post(ruta, json={'nombre': nombre}, headers=headers)


def _nombre(prefijo):
    return '%s-%s' % (prefijo, uuid.uuid4().hex[:10])


@pytest.mark.parametrize(('ruta', 'clave', 'id_col'), RUTAS)
def test_crea_categoria_o_marca_con_digitos_y_tildes(fx, ruta, clave, id_col):
    nombre = _nombre('Férretería Ñ 2026')
    respuesta = _post(fx, ruta, '  %s  ' % nombre)
    assert respuesta.status_code == 201
    datos = respuesta.get_json()
    assert datos['ok'] is True
    assert datos['creada'] is True
    assert datos[clave]['nombre'] == nombre
    assert isinstance(datos[clave][id_col], int)
    fx['cur'].execute(
        'SELECT nombre FROM %s WHERE %s = %%s' % (clave, id_col),
        (datos[clave][id_col],))
    assert fx['cur'].fetchone()['nombre'] == nombre


@pytest.mark.parametrize(('ruta', 'clave', 'id_col'), RUTAS)
def test_duplicado_sin_mayusculas_ni_tildes_devuelve_el_existente(
        fx, ruta, clave, id_col):
    sufijo = uuid.uuid4().hex[:10]
    original = 'Cátálogo Ñandú %s' % sufijo
    # La variante elimina TODAS las tildes, además de cambiar mayúsculas.
    duplicado = '  catalogo nandu %s  ' % sufijo.upper()
    creada = _post(fx, ruta, original).get_json()
    respuesta = _post(fx, ruta, duplicado)
    assert respuesta.status_code == 200
    datos = respuesta.get_json()
    assert datos['ok'] is True
    assert datos['creada'] is False
    assert datos['mensaje'] == 'Ya existía; la seleccioné.'
    assert datos[clave][id_col] == creada[clave][id_col]
    fx['cur'].execute(
        'SELECT COUNT(*) AS total FROM %s WHERE nombre IN (%%s, %%s)'
        % clave, (original, duplicado.strip()))
    assert fx['cur'].fetchone()['total'] == 1


@pytest.mark.parametrize(('ruta', 'clave', 'id_col'), RUTAS)
@pytest.mark.parametrize('nombre', ['', '   ', 'x' * 61])
def test_nombre_invalido_es_400_json(fx, ruta, clave, id_col, nombre):
    respuesta = _post(fx, ruta, nombre)
    assert respuesta.status_code == 400
    datos = respuesta.get_json()
    assert datos['ok'] is False
    assert datos['mensaje']
    assert 'Traceback' not in respuesta.get_data(as_text=True)


@pytest.mark.parametrize(('ruta', 'clave', 'id_col'), RUTAS)
def test_nombre_no_texto_es_400_json_nunca_500(fx, ruta, clave, id_col):
    respuesta = _post(fx, ruta, 123)
    assert respuesta.status_code == 400
    assert respuesta.get_json()['ok'] is False


@pytest.mark.parametrize(('ruta', 'clave', 'id_col'), RUTAS)
def test_sin_csrf_es_400(fx, ruta, clave, id_col):
    respuesta = _post(fx, ruta, _nombre('Sin CSRF'), csrf=False)
    assert respuesta.status_code == 400
    assert respuesta.get_json()['ok'] is False


@pytest.mark.parametrize(('ruta', 'clave', 'id_col'), RUTAS)
def test_vendedor_sin_permiso_productos_recibe_302_o_403_exacto(
        fx, monkeypatch, ruta, clave, id_col):
    monkeypatch.setattr(modulo_app, '_permisos_staff', lambda: set())
    normal = _post(fx, ruta, _nombre('Sin permiso'), ajax=False)
    assert normal.status_code == 302
    assert normal.headers['Location'].endswith('/inicio_personal')
    ajax = _post(fx, ruta, _nombre('Sin permiso'), ajax=True)
    assert ajax.status_code == 403
    assert ajax.get_json()['ok'] is False


def test_nuevos_nombres_aparecen_en_formulario_y_filtros(fx):
    categoria = _post(fx, '/productos/categorias/nueva',
                      _nombre('Categoría visible')).get_json()['categoria']
    marca = _post(fx, '/productos/marcas/nueva',
                  _nombre('Marca visible')).get_json()['marca']

    formulario = fx['cliente'].get('/productos/nuevo')
    assert formulario.status_code == 200
    html = formulario.get_data(as_text=True)
    assert 'id="selectCategoria"' in html
    assert 'id="selectMarca"' in html
    assert 'value="%s"' % categoria['id_categoria'] in html
    assert categoria['nombre'] in html
    assert 'value="%s"' % marca['id_marca'] in html
    assert marca['nombre'] in html
    assert html.count('+ Nueva') == 2
    assert 'id="modalNuevaTaxonomia"' in html
    assert 'prompt(' not in html

    listado = fx['cliente'].get('/productos')
    assert listado.status_code == 200
    html = listado.get_data(as_text=True)
    assert '<option value="%s"' % categoria['nombre'] in html
    assert '<option value="%s"' % marca['nombre'] in html


def test_catalogo_tiene_filtro_de_marca_solo_con_productos_activos(fx):
    activa = _post(fx, '/productos/marcas/nueva',
                   _nombre('Marca activa')).get_json()['marca']
    inactiva = _post(fx, '/productos/marcas/nueva',
                     _nombre('Marca inactiva')).get_json()['marca']
    fx['cur'].execute("""
        INSERT INTO producto (nombre, precio, stock, id_marca, activo)
        VALUES (%s, 10, 2, %s, TRUE), (%s, 10, 2, %s, FALSE)
    """, (_nombre('Producto activo'), activa['id_marca'],
          _nombre('Producto inactivo'), inactiva['id_marca']))

    respuesta = fx['cliente'].get('/catalogo')
    assert respuesta.status_code == 200
    html = respuesta.get_data(as_text=True)
    assert 'id="marcaBtn"' in html
    assert 'id="marcaMenu"' in html
    assert 'MARCAS' in html
    assert 'data-marca="%s"' % activa['nombre'] in html
    assert 'data-marca="%s"' % inactiva['nombre'] not in html
    assert 'function filtrarMarca(' in html


def test_nombre_malicioso_se_escapa_en_las_plantillas(fx):
    payload = '<img src=x onerror=alert(1)>'
    creada = _post(fx, '/productos/categorias/nueva', payload)
    assert creada.status_code == 201
    for ruta in ('/productos/nuevo', '/productos'):
        html = fx['cliente'].get(ruta).get_data(as_text=True)
        assert payload not in html
        assert '&lt;img src=x onerror=alert(1)&gt;' in html
