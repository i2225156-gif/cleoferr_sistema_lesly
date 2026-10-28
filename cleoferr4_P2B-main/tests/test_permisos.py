"""Permisos por módulo del personal (sin BD real).

Todo se prueba con MONKEYPATCH: get_connection_auth / get_connection_tienda
son dobles que graban cada consulta y sirven filas en cola. Nunca se
conecta a Supabase (la de la carpeta tests; por eso NO corren los demás).

Cobertura:
  * catálogo y POR_DEFECTO_VENDEDOR (services/permisos.py);
  * la tabla ruta→permiso desplegada: cada regla existe en app.url_map y
    su vista lleva permiso_required con la clave exacta;
  * before_request del personal: UNA consulta (activo + permisos);
    desactivado o eliminado → session.clear() + flash + /login; tabla
    inexistente → POR_DEFECTO_VENDEDOR y error en el log;
  * permiso_required: admin pasa siempre; vendedor sin permiso → 403
    JSON en AJAX; flash + /inicio_personal si no es AJAX;
  * inicio_personal: admin→dashboard, vendedor→primera permitida, sin
    ninguna→página «sin permisos» con Cerrar sesión;
  * ignoradas static/login/logout y rutas de clientes: 0 consultas;
  * tiene_permiso en el context_processor y sidebar por módulo.
"""
import io
import logging

import pytest

from flask import g, render_template, render_template_string, session

import app as modulo_app
from app import app
from psycopg2 import errors as pg_errors

from services import permisos


# ── Dobles de conexión (cero BD real) ────────────────────────────
class CursorDoble:
    def __init__(self, filas=None, error=None):
        self.filas = list(filas or [])
        self.error = error
        self.consultas = []
        self.ultimo_sql = ''

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))
        self.ultimo_sql = sql
        if self.error is not None:
            raise self.error

    def fetchall(self):
        return self.filas

    def fetchone(self):
        return self.filas[0] if self.filas else None


class ConnDoble:
    def __init__(self, filas=None, error=None):
        self.cur = CursorDoble(filas, error)
        self.cerrada = False

    def cursor(self, dictionary=False, **kwargs):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.cerrada = True


@pytest.fixture
def dobles(monkeypatch):
    """Caja: instala las conexiones dobles y devuelve sus cursores."""
    caja = {}

    def instalar(filas_auth=None, error_auth=None, filas_tienda=None):
        conn_auth = ConnDoble(filas_auth, error_auth)
        monkeypatch.setattr(modulo_app, 'get_connection_auth',
                            lambda: conn_auth)
        if filas_tienda is not None:
            conn_tienda = ConnDoble(filas_tienda)
            monkeypatch.setattr(modulo_app, 'get_connection_tienda',
                                lambda: conn_tienda)
        caja['auth'] = conn_auth
        caja['tienda'] = conn_tienda if filas_tienda is not None else None
        return caja

    return instalar


@pytest.fixture
def cliente():
    return app.test_client()


def _sesion(s, rol='vendedor', uid=7, csrf=None):
    s['usuario_id'] = uid
    s['rol'] = rol
    s['nombre'] = 'Personal de Prueba'
    if csrf:
        s['_csrf_token'] = csrf


# ── 1. Catálogo y valores por defecto ────────────────────────────
def test_catalogo_completo():
    assert set(permisos.CATALOGO) == {
        'ventas', 'pedidos', 'clientes', 'productos', 'inventario',
        'proveedores', 'ofertas', 'reportes', 'alertas', 'compras'}
    assert all(permisos.CATALOGO.values())          # etiquetas no vacías


def test_por_defecto_vendedor():
    assert set(permisos.POR_DEFECTO_VENDEDOR) == \
        {'ventas', 'pedidos', 'clientes', 'alertas'}
    assert set(permisos.POR_DEFECTO_VENDEDOR) <= set(permisos.CATALOGO)


def test_sql_del_auth03_idempotente_y_consistente():
    sql = io.open('db/auth_03_permisos.sql', encoding='utf-8').read()
    assert 'EJECUTAR EN EL PROYECTO AUTH, NO EN TIENDA' in sql
    for clave in permisos.CATALOGO:
        assert "'%s'" % clave in sql, clave        # el CHECK usa el catálogo
    assert 'ON CONFLICT (id_usuario, permiso) DO NOTHING' in sql
    assert 'ON DELETE CASCADE' in sql
    assert 'CREATE TABLE IF NOT EXISTS' in sql     # idempotente


# ── 2. Tabla ruta → permiso desplegada de verdad ─────────────────
def test_rutas_por_permiso_aplicadas_en_las_vistas():
    reglas = {r.rule: r.endpoint for r in app.url_map.iter_rules()}
    errores = []
    for ruta, esperado in permisos.RUTAS_POR_PERMISO.items():
        endpoint = reglas.get(ruta)
        recibido = getattr(app.view_functions.get(endpoint, None),
                           '_permiso_requerido', None)
        if recibido != esperado:
            errores.append((ruta, esperado, endpoint, recibido))
    assert not errores, errores


def test_activas_sin_cambio():
    # /productos: antes solo login_required; ahora lleva su permiso.
    assert getattr(app.view_functions['productos'],
                   '_permiso_requerido', None) == 'productos'
    # eliminar_* y /admin/*: siguen SOLO-ADMIN (sin cambio).
    for endpoint in ('eliminar_producto', 'eliminar_cliente',
                     'eliminar_proveedor', 'admin_dashboard',
                     'admin_reservas_liberar', 'admin_pagos_disparar'):
        assert not hasattr(app.view_functions[endpoint],
                           '_permiso_requerido'), endpoint
    # APIs del carrito: sin permiso (las usan también los clientes).
    for endpoint in ('api_consultar_ruc', 'api_consultar_dni'):
        assert not hasattr(app.view_functions[endpoint],
                           '_permiso_requerido')


# ── 3. before_request del personal ───────────────────────────────
def test_una_consulta_carga_permisos(dobles):
    caja = dobles(filas_auth=[
        {'activo': True, 'permiso': 'ventas'},
        {'activo': True, 'permiso': 'pedidos'},
        {'activo': True, 'permiso': 'clientes'},
        {'activo': True, 'permiso': 'alertas'},
    ])
    with app.test_request_context('/pedidos'):
        _sesion(session)
        assert modulo_app._cargar_permisos_personal() is None
        assert len(caja['auth'].cur.consultas) == 1              # UNA consulta
        assert 'usuario_permiso' in caja['auth'].cur.ultimo_sql
        assert g.permisos == {'ventas', 'pedidos', 'clientes', 'alertas'}
    with app.test_request_context('/pedidos'):
        _sesion(session, rol='administrador', uid=1)
        assert modulo_app._cargar_permisos_personal() is None
        assert g.permisos == set(permisos.CATALOGO)              # admin: todo


def test_desactivado_limpia_sesion_y_manda_a_login(dobles, cliente):
    caja = dobles(filas_auth=[{'activo': False, 'permiso': 'ventas'}])
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/productos', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/login')
    assert len(caja['auth'].cur.consultas) == 1
    with cliente.session_transaction() as s:
        assert 'usuario_id' not in s                              # clear()
        assert 'desactivada' in str(s.get('_flashes'))            # flash


def test_cuenta_eliminada_manda_a_login(dobles, cliente):
    caja = dobles(filas_auth=[])                    # 0 filas: ya no existe
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/pedidos', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/login')
    assert len(caja['auth'].cur.consultas) == 1
    with cliente.session_transaction() as s:
        assert 'usuario_id' not in s


def test_tabla_inexistente_usa_por_defecto(dobles, cliente, caplog):
    error = pg_errors.UndefinedTable(
        'relation "usuario_permiso" does not exist')
    caja = dobles(error_auth=error)
    with caplog.at_level(logging.ERROR):
        with cliente.session_transaction() as s:
            _sesion(s)
        resp = cliente.get('/inicio_personal', follow_redirects=False)
    assert resp.status_code == 302
    # Por defecto (ventas sí): aterriza en el POS, no bloquea al personal.
    assert resp.headers['Location'].endswith('/ventas/nueva')
    assert 'usuario_permiso' in caplog.text                     # en el log
    assert len(caja['auth'].cur.consultas) == 1


def test_rutas_ignoradas_ejecutan_cero_consultas(dobles, cliente):
    caja = dobles(filas_auth=[])
    with cliente.session_transaction() as s:
        _sesion(s)
    assert cliente.get('/privacidad').status_code == 200        # cliente
    assert cliente.get('/login').status_code == 200         # login
    assert cliente.get('/logout', follow_redirects=False).status_code == 302
    assert len(caja['auth'].cur.consultas) == 0


def test_ruta_compartida_del_personal_si_chequea(dobles, cliente):
    caja = dobles(filas_auth=[{'activo': True, 'permiso': None}])
    with cliente.session_transaction() as s:
        _sesion(s)
    assert cliente.get('/cambiar_clave').status_code == 200     # personal
    assert len(caja['auth'].cur.consultas) == 1


# ── 4. permiso_required ──────────────────────────────────────────
def test_vendedor_sin_permiso_redirige_con_flash(dobles, cliente):
    caja = dobles(filas_auth=[{'activo': True, 'permiso': 'ventas'}])
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/pedidos', follow_redirects=False)     # NO tiene
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/inicio_personal')
    with cliente.session_transaction() as s:
        assert 'permisos' in str(s.get('_flashes'))


def test_vendedor_con_permiso_sigue_a_la_vista(dobles, cliente):
    # /productos/nuevo: before_request carga 'productos' (auth doble) y la
    # vista usa la conexión de TIENDA doble (2 SELECT: categoria, marca):
    # cero BD real.
    caja = dobles(filas_auth=[{'activo': True, 'permiso': 'productos'}],
                  filas_tienda=[])
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/productos/nuevo')
    assert resp.status_code == 200
    assert len(caja['tienda'].cur.consultas) == 2


def test_vendedor_ajax_recibe_403_json(dobles, cliente):
    caja = dobles(filas_auth=[{'activo': True, 'permiso': 'ventas'}])
    with cliente.session_transaction() as s:
        _sesion(s, csrf='tokentest')
    resp = cliente.post(
        '/alertas/resolver/1',
        data={'csrf_token': 'tokentest'},
        headers={'X-Requested-With': 'XMLHttpRequest'},
        follow_redirects=False)
    assert resp.status_code == 403
    datos = resp.get_json()
    assert datos['ok'] is False
    assert 'permisos' in datos['mensaje'].lower()


def test_tiene_permiso_en_request_context():
    with app.test_request_context('/x'):
        session['rol'] = 'vendedor'
        g.permisos = {'ventas'}
        assert modulo_app.tiene_permiso('ventas') is True
        assert modulo_app.tiene_permiso('productos') is False
        session['rol'] = 'administrador'
        assert modulo_app.tiene_permiso('compras') is True     # admin: todo
    with app.test_request_context('/x'):
        session['rol'] = 'vendedor'                            # sin g
        assert modulo_app.tiene_permiso('ventas') is False


# ── 5. inicio_personal ───────────────────────────────────────────
def test_inicial_admin_al_dashboard(dobles, cliente):
    dobles(filas_auth=[{'activo': True, 'permiso': None}])
    with cliente.session_transaction() as s:
        _sesion(s, rol='administrador', uid=1)
    resp = cliente.get('/inicio_personal', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/admin/dashboard')


def test_inicial_vendedor_primera_permitida(dobles, cliente):
    dobles(filas_auth=[
        {'activo': True, 'permiso': 'reportes'},
        {'activo': True, 'permiso': 'alertas'},
        {'activo': True, 'permiso': 'productos'},
        {'activo': True, 'permiso': 'clientes'},
        {'activo': True, 'permiso': 'pedidos'},
    ])
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/inicio_personal', follow_redirects=False)
    # El ORDEN manda: sin 'ventas', la primera permitida es /pedidos.
    assert resp.headers['Location'].endswith('/pedidos')
    dobles(filas_auth=[
        {'activo': True, 'permiso': 'clientes'},
        {'activo': True, 'permiso': 'ventas'},
    ])
    resp = cliente.get('/inicio_personal', follow_redirects=False)
    assert resp.headers['Location'].endswith('/ventas/nueva')


def test_inicial_vendedor_sin_nada_pagina_simple(dobles, cliente):
    dobles(filas_auth=[{'activo': True, 'permiso': None}])       # 0 permisos
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/inicio_personal')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Sin permisos' in html
    assert '/logout' in html                          # Cerrar sesión


def test_inicial_anonimo_al_login(cliente):
    resp = cliente.get('/inicio_personal', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/login')


def test_cliente_ruta_personal_redirige_y_no_consulta(dobles, cliente):
    caja = dobles(filas_auth=[])
    with cliente.session_transaction() as s:
        s['usuario_id'] = 55
        s['rol'] = 'cliente'
        s['nombre'] = 'Cliente Prueba'
    resp = cliente.get('/inicio_personal', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/catalogo')
    clic = cliente.get('/productos', follow_redirects=False)
    assert clic.status_code == 302
    # El decorador primero manda al aterrizaje (que decide /catalogo).
    assert clic.headers['Location'].endswith('/inicio_personal')
    # before_request NO consultó (cliente descarta al inicio).
    assert len(caja['auth'].cur.consultas) == 0


# ── 6. tiene_permiso en el context_processor + sidebar ───────────
NOMBRES = ('/productos', '/admin/ofertas', '/pedidos', '/clientes',
           '/proveedores', '/inventario', '/alertas', '/reportes',
           '/caja', '/admin/dashboard')


def _visibles():
    html = render_template('_sidebar_admin.html')
    return {n: ('href="/%s"' % n.strip('/')) in html for n in NOMBRES}


def test_sidebar_admin_ve_todo():
    with app.test_request_context('/x'):
        session['rol'] = 'administrador'
        visibles = _visibles()
    assert all(visibles.values())


def test_sidebar_vendedor_solo_sus_modulos():
    with app.test_request_context('/x'):
        session['rol'] = 'vendedor'
        g.permisos = {'ventas', 'pedidos', 'clientes', 'alertas'}
        visibles = _visibles()
    assert visibles['/caja']                      # permiso ventas
    assert visibles['/pedidos'] and visibles['/clientes']
    assert visibles['/alertas']
    assert not visibles['/productos'] and not visibles['/admin/ofertas']
    assert not visibles['/proveedores'] and not visibles['/inventario']
    assert not visibles['/reportes'] and not visibles['/admin/dashboard']


def test_context_processor_registrado():
    with app.test_request_context('/x'):
        session['rol'] = 'vendedor'
        g.permisos = {'ventas'}
        html = render_template_string("{{ tiene_permiso('ventas') }}-"
                                      "{{ tiene_permiso('productos') }}")
    assert html == 'True-False'
