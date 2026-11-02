"""Cobertura completa de rutas: permiso por módulo y solo-admin.

- Test A recorre app.url_map y exige que TODA ruta fuera de la lista
  blanca explícita de rutas públicas/de cliente esté protegida por
  permiso_required (con la clave de services/permisos.RUTAS_POR_PERMISO)
  o por admin_required. No se omite ninguna ruta.
- Test B/C sustituyen la vista por un stub conservando los decoradores
  reales y llaman con el método correcto: se comprueban los códigos
  exactos (302/403) sin ejecutar vistas ni consultar la BD.
- Sin filas SQL simuladas ni conexiones dobles: los permisos se inyectan
  en _permisos_staff (la consulta de before_request se neutraliza en el
  fixture, porque aquí solo se prueban los decoradores).
"""
import pytest

import app as modulo_app
from app import app

from services import permisos


TOKEN = 'csrf-cobertura'

# Rutas públicas, de cliente o compartidas por cliente/personal: no
# llevan permiso de módulo (solo login, o nada). Explícitas por endpoint.
PUBLICOS = {
    'static',
    # acceso / recuperación / registro
    'inicio', 'login', 'logout', 'login_cliente', 'registro_cliente',
    'verificar_registro', 'recuperar_contrasena', 'restablecer_contrasena',
    'privacidad',
    # catálogo, carrito y pago del cliente
    'catalogo_cliente', 'carrito', 'carrito_agregar',
    'carrito_actualizar', 'carrito_confirmar_v2', 'carrito_precios',
    'pago_simulado', 'pago_simulado_procesar', 'pedido_aceptado',
    # pedidos / comprobantes / perfil propios
    'mis_pedidos', 'mis_comprobantes', 'mi_comprobante', 'perfil',
    'perfil_actualizar_documento', 'pedido_cancelar',
    # integraciones y consultas usadas también por clientes
    'webhook_pago', 'api_consultar_ruc', 'api_consultar_dni',
    # compartidas del personal (solo exigen sesión)
    'inicio_personal', 'cambiar_clave',
}


def _es_publica(endpoint):
    # 'validar_*' no tiene rutas hoy; se deja el prefijo previsto.
    return endpoint in PUBLICOS or endpoint.startswith('validar_')


def _marcas(endpoint):
    vista = app.view_functions[endpoint]
    return (getattr(vista, '_permiso_requerido', None),
            getattr(vista, '_solo_admin', False))


@pytest.fixture()
def entorno(monkeypatch):
    """Neutraliza la carga de permisos desde la BD y permite inyectarlos."""
    monkeypatch.setitem(
        app.before_request_funcs, None,
        [f for f in app.before_request_funcs[None]
         if f is not modulo_app._cargar_permisos_personal])

    def inyectar(claves):
        monkeypatch.setattr(modulo_app, '_permisos_staff',
                            lambda: set(claves))

    return inyectar


@pytest.fixture()
def cliente():
    return app.test_client()


def _sesion(cliente, rol):
    with cliente.session_transaction() as s:
        s['usuario_id'] = 1 if rol == 'administrador' else 7
        s['rol'] = rol
        s['nombre'] = 'Personal de Prueba'
        s['_csrf_token'] = TOKEN


def _stub(monkeypatch, endpoint):
    """Sustituye la vista por un stub conservando sus decoradores."""
    original = app.view_functions[endpoint]
    clave, solo_admin = _marcas(endpoint)

    def respuesta(*args, **kwargs):
        return 'STUB:%s' % endpoint

    respuesta.__name__ = 'stub_%s' % endpoint
    vista = respuesta
    if solo_admin:
        vista = modulo_app.admin_required(vista)
    if clave:
        vista = modulo_app.permiso_required(clave)(vista)
    vista = modulo_app.login_required(vista)
    monkeypatch.setitem(app.view_functions, endpoint, vista)


# ── Test A: url_map completo ─────────────────────────────────────
def test_a_toda_ruta_protegida_y_mapeada():
    desprotegidas = []
    mal_mapeadas = []
    admin = []
    for regla in app.url_map.iter_rules():
        endpoint = regla.endpoint
        if _es_publica(endpoint):
            continue
        clave, solo_admin = _marcas(endpoint)
        if solo_admin:
            admin.append(regla.rule)
            continue
        if clave is None:
            desprotegidas.append('%s [%s]' % (regla.rule, endpoint))
            continue
        esperado = permisos.RUTAS_POR_PERMISO.get(regla.rule)
        if clave != esperado:
            mal_mapeadas.append((regla.rule, endpoint, clave, esperado))

    reglas = {r.rule for r in app.url_map.iter_rules()}
    faltantes = sorted(set(permisos.RUTAS_POR_PERMISO) - reglas)

    assert not desprotegidas, (
        'Rutas sin permiso_required ni admin_required:\n  '
        + '\n  '.join(desprotegidas))
    assert not mal_mapeadas, (
        'Rutas con permiso distinto a RUTAS_POR_PERMISO '
        '(ruta, endpoint, recibido, esperado):\n  '
        + '\n  '.join(map(str, mal_mapeadas)))
    assert not faltantes, (
        'RUTAS_POR_PERMISO apunta a rutas inexistentes:\n  '
        + '\n  '.join(faltantes))
    assert admin, 'No se detectó ninguna ruta solo-admin'


# ── Test A2: el mapa de protección (services/permisos) ───────────
def test_a2_mapa_de_rutas_solo_admin_consistente():
    """El mapa RUTAS_SOLO_ADMIN refleja EXACTAMENTE las rutas del app.

    - toda regla del mapa existe y lleva admin_required;
    - toda ruta solo-admin de la app está en el mapa (el mapa no se
      queda corto: una ruta nueva sin mapear queda fuera de la
      cobertura).
    """
    reglas = {r.rule: r.endpoint for r in app.url_map.iter_rules()}
    mapa = permisos.RUTAS_SOLO_ADMIN

    inexistentes = sorted(set(mapa) - set(reglas))
    endpoints_malos = sorted(
        (ruta, mapa[ruta], reglas.get(ruta))
        for ruta in mapa
        if reglas.get(ruta) != mapa[ruta]
    )
    sin_admin = sorted(
        ruta for ruta in mapa
        if not getattr(app.view_functions.get(reglas.get(ruta)),
                       '_solo_admin', False)
    )
    fuera_del_mapa = sorted(
        regla.rule for regla in app.url_map.iter_rules()
        if getattr(app.view_functions.get(regla.endpoint),
                   '_solo_admin', False)
        and regla.rule not in mapa
    )

    assert not inexistentes, ('Rutas del mapa que no existen:\n  '
                              + '\n  '.join(inexistentes))
    assert not endpoints_malos, ('Ruta->endpoint del mapa incorrecto: '
                                 '%s' % (endpoints_malos,))
    assert not sin_admin, ('Rutas del mapa sin admin_required:\n  '
                            + '\n  '.join(sin_admin))
    assert not fuera_del_mapa, ('Rutas solo-admin sin registrar en el '
                                 'mapa de protección:\n  '
                                 + '\n  '.join(fuera_del_mapa))


# ── Test B: permiso_required (GET y AJAX) ────────────────────────
def test_b_vendedor_sin_permiso_get_redirige(cliente, monkeypatch, entorno):
    entorno(set())
    _stub(monkeypatch, 'pedidos')
    _sesion(cliente, 'vendedor')
    r = cliente.get('/pedidos', follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/inicio_personal')


def test_b_vendedor_sin_permiso_ajax_es_403_json(cliente, monkeypatch,
                                                 entorno):
    entorno(set())
    _stub(monkeypatch, 'alertas_resolver')
    _sesion(cliente, 'vendedor')
    r = cliente.post(
        '/alertas/resolver/1',
        headers={'X-CSRFToken': TOKEN,
                 'X-Requested-With': 'XMLHttpRequest'},
        follow_redirects=False)
    assert r.status_code == 403
    datos = r.get_json()
    assert datos['ok'] is False
    assert 'permisos' in datos['mensaje'].lower()


def test_b_con_el_permiso_y_admin_llegan_al_stub(cliente, monkeypatch,
                                                 entorno):
    _stub(monkeypatch, 'pedidos')
    _stub(monkeypatch, 'alertas_resolver')

    entorno({'pedidos', 'alertas'})
    _sesion(cliente, 'vendedor')
    assert cliente.get('/pedidos').get_data(as_text=True) == 'STUB:pedidos'
    assert cliente.post(
        '/alertas/resolver/1',
        headers={'X-CSRFToken': TOKEN}).get_data(as_text=True) == \
        'STUB:alertas_resolver'

    entorno(set())
    _sesion(cliente, 'administrador')
    assert cliente.get('/pedidos').get_data(as_text=True) == 'STUB:pedidos'
    assert cliente.post(
        '/alertas/resolver/1',
        headers={'X-CSRFToken': TOKEN}).get_data(as_text=True) == \
        'STUB:alertas_resolver'


# ── Test C: rutas solo administrador ─────────────────────────────
CASOS_ADMIN = (
    ('/productos/eliminar/1', 'POST', 'eliminar_producto'),
    ('/clientes/eliminar/1', 'POST', 'eliminar_cliente'),
    ('/proveedores/eliminar/1', 'POST', 'eliminar_proveedor'),
    ('/admin/dashboard', 'GET', 'admin_dashboard'),
    ('/admin/pagos_simulados', 'GET', 'admin_pagos_simulados'),
    ('/admin/reservas', 'GET', 'admin_reservas'),
    # Panel de usuarios y permisos (RF13/RF26): SOLO-ADMIN.
    ('/admin/usuarios', 'GET', 'admin_usuarios'),
    ('/admin/usuarios/nuevo', 'POST', 'admin_usuarios_nuevo'),
    ('/admin/usuarios/1/permisos', 'POST', 'admin_usuarios_permisos'),
    ('/admin/usuarios/1/estado', 'POST', 'admin_usuarios_estado'),
    ('/admin/usuarios/1/desbloquear', 'POST', 'admin_usuarios_desbloquear'),
)


def test_c_vendedor_con_todos_los_permisos_no_entra(cliente, monkeypatch,
                                                    entorno):
    entorno(set(permisos.CATALOGO))
    _sesion(cliente, 'vendedor')
    for ruta, metodo, endpoint in CASOS_ADMIN:
        _stub(monkeypatch, endpoint)
        normal = cliente.open(
            ruta, method=metodo, headers={'X-CSRFToken': TOKEN},
            follow_redirects=False)
        assert normal.status_code not in (400, 405), (ruta, normal.status_code)
        assert normal.status_code == 302, (ruta, normal.status_code)
        assert normal.headers['Location'].endswith('/inicio_personal')

        ajax = cliente.open(
            ruta, method=metodo,
            headers={'X-CSRFToken': TOKEN,
                     'X-Requested-With': 'XMLHttpRequest'},
            follow_redirects=False)
        assert ajax.status_code not in (400, 405), (ruta, ajax.status_code)
        assert ajax.status_code == 403, (ruta, ajax.status_code)
        assert ajax.get_json()['ok'] is False
