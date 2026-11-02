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
    def __init__(self, filas=None, error=None, rowcount=None):
        self.filas = list(filas or [])
        self.error = error
        self.consultas = []
        self.ultimo_sql = ''
        # rowcount fijo (SEND/DML): None equivale a 0 salvo que el test
        # lo configure al construir el doble.
        self.rowcount = 0 if rowcount is None else rowcount

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
    def __init__(self, filas=None, error=None, rowcount=None):
        self.cur = CursorDoble(filas, error, rowcount)
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
    # auth_03 no CREO tablas: usuario_permiso/permiso/rol_permiso ya
    # existen (verificado con information_schema); siembra en 3 pasos.
    sql = io.open('db/auth_03_permisos.sql', encoding='utf-8').read()
    assert 'EJECUTAR EN EL PROYECTO AUTH, NO EN TIENDA' in sql
    # 1) catálogo de permiso con activo=TRUE, idempotente
    for clave in permisos.CATALOGO:
        assert "('%s')" % clave in sql, clave
    assert 'activo' in sql and 'TRUE' in sql
    # 2) plantilla del rol vendedor en rol_permiso
    assert 'rol_permiso' in sql
    assert "LOWER(TRIM(r.nombre)) = 'vendedor'" in sql
    for semilla in ('ventas', 'pedidos', 'clientes', 'alertas'):
        assert "('%s')" % semilla in sql
    # 3) usuario_permiso: copia con auditoría; asignado_por NULL porque
    #    la columna admite NULL (verificado), fecha_asignacion now().
    assert 'usuario_permiso' in sql
    assert 'asignado_por' in sql and 'fecha_asignacion' in sql
    assert 'now()' in sql
    assert 'NULL' in sql
    # idempotencia: sin NOT EXISTS ni falla (al menos 2 apariciones)
    assert sql.count('NOT EXISTS') >= 2
    assert 'ORDER IMPORTANTE' in sql.upper() or 'ORDEN IMPORTANTE' in sql.upper()


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
        {'activo': True, 'permiso': 'ventas',
         'plantilla_sembrada': True},
        {'activo': True, 'permiso': 'pedidos',
         'plantilla_sembrada': True},
        {'activo': True, 'permiso': 'clientes',
         'plantilla_sembrada': True},
        {'activo': True, 'permiso': 'alertas',
         'plantilla_sembrada': True},
    ])
    with app.test_request_context('/pedidos'):
        _sesion(session)
        assert modulo_app._cargar_permisos_personal() is None
        assert len(caja['auth'].cur.consultas) == 1              # UNA consulta
        assert 'usuario_permiso' in caja['auth'].cur.ultimo_sql
        assert 'p.activo = TRUE' in caja['auth'].cur.ultimo_sql  # JOIN real
        assert 'plantilla_sembrada' in caja['auth'].cur.ultimo_sql
        assert g.permisos == {'ventas', 'pedidos', 'clientes', 'alertas'}
    with app.test_request_context('/pedidos'):
        _sesion(session, rol='administrador', uid=1)
        assert modulo_app._cargar_permisos_personal() is None
        assert g.permisos == set(permisos.CATALOGO)              # admin: todo


def test_vendedor_usa_exactamente_sus_filas(dobles):
    caja = dobles(filas_auth=[{'activo': True, 'permiso': 'ventas',
                               'plantilla_sembrada': True},
                              {'activo': True, 'permiso': 'productos',
                               'plantilla_sembrada': True}])
    with app.test_request_context('/productos'):
        _sesion(session)
        assert modulo_app._cargar_permisos_personal() is None
        # Sus filas EXACTAS (no plantilla, no todos).
        assert g.permisos == {'ventas', 'productos'}


def test_vendedor_plantilla_vacia_usa_por_defecto(dobles, cliente):
    # Estructura REAL + auth_03 SIN EJECUTAR: usuario_permiso, permiso y
    # rol_permiso VACÍAS → el LEFT JOIN devuelve UNA fila [activo=True,
    # permiso=None, plantilla_sembrada=False]. El vendedor NO queda sin
    # acceso: POR_DEFECTO_VENDEDOR (estado actual, plantilla sin sembrar).
    caja = dobles(filas_auth=[{'activo': True, 'permiso': None,
                               'plantilla_sembrada': False}])
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/inicio_personal', follow_redirects=False)
    assert resp.status_code == 302
    # La plantilla incluye 'ventas': aterriza en el POS.
    assert resp.headers['Location'].endswith('/ventas/nueva')


def test_vendedor_plantilla_sembrada_sin_filas_queda_bloqueado(dobles,
                                                               cliente):
    # auth_03 YA APLICADO: rol_permiso del rol vendedor TIENE filas →
    # un vendedor en CERO permisos queda SIN panel (decisión explícita
    # de revocación total, no falla de siembra) → sin_permisos.html.
    caja = dobles(filas_auth=[{'activo': True, 'permiso': None,
                               'plantilla_sembrada': True}])
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/inicio_personal', follow_redirects=True)
    assert resp.status_code == 200
    cuerpo = resp.get_data(as_text=True)
    assert 'Sin permisos' in cuerpo
    assert '/logout' in cuerpo


def test_desactivado_limpia_sesion_y_manda_a_login(dobles, cliente):
    caja = dobles(filas_auth=[{'activo': False, 'permiso': 'ventas',
                               'plantilla_sembrada': True}])
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
    caja = dobles(filas_auth=[{'activo': True, 'permiso': None,
                               'plantilla_sembrada': False}])
    with cliente.session_transaction() as s:
        _sesion(s)
    assert cliente.get('/cambiar_clave').status_code == 200     # personal
    assert len(caja['auth'].cur.consultas) == 1


# ── 4. permiso_required ──────────────────────────────────────────
def test_vendedor_sin_permiso_redirige_con_flash(dobles, cliente):
    caja = dobles(filas_auth=[{'activo': True, 'permiso': 'ventas',
                               'plantilla_sembrada': True}])
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
    caja = dobles(filas_auth=[{'activo': True, 'permiso': 'productos',
                               'plantilla_sembrada': True}],
                  filas_tienda=[])
    with cliente.session_transaction() as s:
        _sesion(s)
    resp = cliente.get('/productos/nuevo')
    assert resp.status_code == 200
    assert len(caja['tienda'].cur.consultas) == 2


def test_vendedor_ajax_recibe_403_json(dobles, cliente):
    caja = dobles(filas_auth=[{'activo': True, 'permiso': 'ventas',
                               'plantilla_sembrada': True}])
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
        {'activo': True, 'permiso': 'reportes',
         'plantilla_sembrada': True},
        {'activo': True, 'permiso': 'alertas',
         'plantilla_sembrada': True},
        {'activo': True, 'permiso': 'productos',
         'plantilla_sembrada': True},
        {'activo': True, 'permiso': 'clientes',
         'plantilla_sembrada': True},
        {'activo': True, 'permiso': 'pedidos',
         'plantilla_sembrada': True},
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


def test_plantilla_sin_permisos_existe_con_cerrar_sesion():
    # La página sigue existiendo como camino defensivo; con las tablas
    # vacías el before_request ya aplica POR_DEFECTO_VENDEDOR.
    with app.test_request_context('/x'):
        html = render_template('sin_permisos.html')
    assert 'Sin permisos' in html
    assert '/logout' in html                      # Cerrar sesión


def test_vendedor_forzado_sin_permisos_ve_la_pagina():
    # caso forzado (sin defaults aplicados)
    with app.test_request_context('/inicio_personal'):
        g.permisos = set()
        session['usuario_id'] = 7
        session['rol'] = 'vendedor'
        resp = app.make_response(app.view_functions['inicio_personal']())
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Sin permisos' in html
        assert '/logout' in html


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


# ── 7. Helpers de auditoría (dar/quitar) sobre la estructura REAL ──
def test_dar_permiso_inserta_con_auditoria():
    conn = ConnDoble(rowcount=1)      # una fila INSERTADA
    hecho = permisos.dar_permiso(conn, id_admin=3, id_usuario=9,
                                 clave='productos')
    assert hecho is True
    sql, params = conn.cur.consultas[0]
    assert 'INSERT INTO usuario_permiso' in sql
    assert 'asignado_por' in sql and 'fecha_asignacion' in sql
    assert 'now()' in sql.lower()
    # Idempotente + auditable: sin duplicar y con responsable+momento.
    assert 'ON CONFLICT (id_usuario, id_permiso) DO NOTHING' in sql
    assert 'p.activo = TRUE' in sql                  # solo permisos activos
    assert params == (9, 3, 'productos')             # (usuario, admin, clave)


def test_dar_permiso_clave_inexistente_o_inactiva_no_inserta():
    conn = ConnDoble(rowcount=0)      # permiso inexistente o inactivo
    assert permisos.dar_permiso(conn, None, 9, 'relampago') is False


def test_quitar_permiso_borra_la_fila():
    conn = ConnDoble(rowcount=1)      # una fila BORRADA
    hecho = permisos.quitar_permiso(conn, 9, 'ventas')
    assert hecho is True
    sql, params = conn.cur.consultas[0]
    assert 'DELETE FROM usuario_permiso' in sql
    assert 'USING permiso' in sql                     # por nombre, no id
    assert params == (9, 'ventas')


def test_quitar_permiso_sin_fila_no_borra_nada():
    conn = ConnDoble(rowcount=0)
    assert permisos.quitar_permiso(conn, 9, 'compras') is False
