"""Subida de imágenes a Supabase Storage con urllib (sin red real).

Cubre: falta de configuración, subida correcta con la URL pública, error de
red/HTTP y que la clave de servicio nunca aparezca en los logs.
"""
import logging
import urllib.error

import services.almacenamiento as almacenamiento


class _Respuesta:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _limpiar_entorno(monkeypatch):
    for nombre in ('SUPABASE_TIENDA_PROJECT_URL', 'SUPABASE_TIENDA_SECRET_KEY',
                   'SUPABASE_BUCKET_PRODUCTOS'):
        monkeypatch.delenv(nombre, raising=False)


def test_sin_configuracion_devuelve_none(monkeypatch):
    _limpiar_entorno(monkeypatch)
    assert almacenamiento.subir(b'contenido', 'foto.png', 'image/png') is None


def test_sube_y_devuelve_la_url_publica(monkeypatch):
    monkeypatch.setenv('SUPABASE_TIENDA_PROJECT_URL',
                       'https://proyecto.supabase.co/')
    monkeypatch.setenv('SUPABASE_TIENDA_SECRET_KEY', 'clave-de-prueba')
    monkeypatch.setenv('SUPABASE_BUCKET_PRODUCTOS', 'productos')
    visto = {}

    def urlopen_falso(peticion, timeout=None):
        visto['url'] = peticion.full_url
        visto['cabeceras'] = {k.lower(): v
                              for k, v in peticion.header_items()}
        visto['datos'] = peticion.data
        visto['timeout'] = timeout
        return _Respuesta(200)

    monkeypatch.setattr(almacenamiento.urllib.request, 'urlopen',
                        urlopen_falso)

    url = almacenamiento.subir(b'\x89PNG\r\n\x1a\n', 'foto.png', 'image/png')

    assert url == ('https://proyecto.supabase.co/storage/v1/object/public/'
                   'productos/foto.png')
    assert visto['url'] == ('https://proyecto.supabase.co/storage/v1/object/'
                            'productos/foto.png')
    assert visto['cabeceras']['authorization'] == 'Bearer clave-de-prueba'
    assert visto['cabeceras']['apikey'] == 'clave-de-prueba'
    assert visto['cabeceras']['content-type'] == 'image/png'
    assert visto['datos'] == b'\x89PNG\r\n\x1a\n'
    assert visto['timeout'] and visto['timeout'] <= 15


def test_bucket_por_defecto_productos(monkeypatch):
    monkeypatch.setenv('SUPABASE_TIENDA_PROJECT_URL',
                       'https://proyecto.supabase.co')
    monkeypatch.setenv('SUPABASE_TIENDA_SECRET_KEY', 'clave-de-prueba')
    monkeypatch.delenv('SUPABASE_BUCKET_PRODUCTOS', raising=False)
    visto = {}

    def urlopen_falso(peticion, timeout=None):
        visto['url'] = peticion.full_url
        return _Respuesta(200)

    monkeypatch.setattr(almacenamiento.urllib.request, 'urlopen',
                        urlopen_falso)

    assert almacenamiento.subir(b'x', 'foto.png', 'image/png').endswith(
        '/public/productos/foto.png')
    assert '/object/productos/foto.png' in visto['url']


def test_error_de_red_devuelve_none_y_no_filtra_la_clave(monkeypatch, caplog):
    monkeypatch.setenv('SUPABASE_TIENDA_PROJECT_URL',
                       'https://proyecto.supabase.co')
    monkeypatch.setenv('SUPABASE_TIENDA_SECRET_KEY', 'clave-muy-secreta')
    monkeypatch.setenv('SUPABASE_BUCKET_PRODUCTOS', 'productos')

    def urlopen_falso(peticion, timeout=None):
        raise urllib.error.URLError('sin red')

    monkeypatch.setattr(almacenamiento.urllib.request, 'urlopen',
                        urlopen_falso)
    caplog.set_level(logging.WARNING)

    assert almacenamiento.subir(b'x', 'foto.png', 'image/png') is None
    assert 'clave-muy-secreta' not in caplog.text


def test_error_http_devuelve_none_y_no_filtra_la_clave(monkeypatch, caplog):
    monkeypatch.setenv('SUPABASE_TIENDA_PROJECT_URL',
                       'https://proyecto.supabase.co')
    monkeypatch.setenv('SUPABASE_TIENDA_SECRET_KEY', 'clave-muy-secreta')
    monkeypatch.setenv('SUPABASE_BUCKET_PRODUCTOS', 'productos')

    def urlopen_falso(peticion, timeout=None):
        raise urllib.error.HTTPError(peticion.full_url, 400, 'Bad Request',
                                     None, None)

    monkeypatch.setattr(almacenamiento.urllib.request, 'urlopen',
                        urlopen_falso)
    caplog.set_level(logging.WARNING)

    assert almacenamiento.subir(b'x', 'foto.png', 'image/png') is None
    assert 'clave-muy-secreta' not in caplog.text
