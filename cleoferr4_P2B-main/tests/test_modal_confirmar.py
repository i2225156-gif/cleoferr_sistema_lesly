"""Componente global de diálogos (C3a+C3b): parcial incluido UNA vez en base.html.

C3a: marcadores en el HTML renderizado (sin ejecutar JS): el modal viaja
exactamente una vez por página (cliente y admin), la API global está
declarada y el aviso de degradación fail-closed nace VISIBLE (el JS lo
oculta al cargar).

C3b: las páginas de cliente (carrito, catálogo) llaman a la API del
componente (confirmarAccion/avisoAccion) y sus fuentes ya no contienen
confirm()/alert() nativos (el propio componente sí, como degradación).
"""
from pathlib import Path

from app import app

_TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'


def _sesion_admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


def _marcadores(html):
    """Conteos de los marcadores que el componente pone en la página."""
    return {
        'modal': html.count('id="modalConfirmar"'),
        'titulo': html.count('id="modalConfirmarTitulo"'),
        'mensaje': html.count('id="modalConfirmarMensaje"'),
        'toast': html.count('id="toastAvisos"'),
        'aviso_deg': html.count('id="avisoSinDialogos"'),
        'fn_confirmar': html.count('function confirmarAccion'),
        'fn_aviso': html.count('function avisoAccion'),
    }


def test_componente_una_vez_pagina_cliente():
    # /catalogo es pública y hereda base.html directamente
    html = app.test_client().get('/catalogo').get_data(as_text=True)
    m = _marcadores(html)
    assert m['modal'] == 1
    assert m['titulo'] == 1
    assert m['mensaje'] == 1
    assert m['toast'] == 1
    assert m['fn_confirmar'] == 1
    assert m['fn_aviso'] == 1


def test_componente_una_vez_pagina_admin():
    # /productos extiende admin_base, que hereda de base: mismo parcial,
    # sin duplicar el modal (id único) ni el script (funciones únicas)
    html = _sesion_admin().get('/productos').get_data(as_text=True)
    m = _marcadores(html)
    assert m['modal'] == 1
    assert m['titulo'] == 1
    assert m['mensaje'] == 1
    assert m['toast'] == 1
    assert m['fn_confirmar'] == 1
    assert m['fn_aviso'] == 1


def test_aviso_degradacion_fail_closed_visible_sin_js():
    # El aviso nace VISIBLE (sin atributo hidden) y es el JS quien lo
    # oculta al cargar: sin JS (o si el script falla antes), se ve la
    # explicación de que los diálogos no están disponibles.
    html = app.test_client().get('/catalogo').get_data(as_text=True)
    assert '<div id="avisoSinDialogos"' in html
    assert '<div id="avisoSinDialogos" hidden' not in html
    assert "getElementById('avisoSinDialogos')" in html
    assert 'avisoDeg.hidden = true' in html


def test_api_global_y_contrato_declarativo():
    html = app.test_client().get('/catalogo').get_data(as_text=True)
    # API global expuesta
    assert 'window.confirmarAccion' in html
    assert 'window.avisoAccion' in html
    # Contrato declarativo: formularios y enlaces por data-confirm
    assert "hasAttribute('data-confirm')" in html
    assert "closest('a[data-confirm]')" in html
    assert 'form.dataset.confirmado' in html
    # Reenvío conservando el botón pulsado (name/value) y el CSRF
    assert 'form.requestSubmit' in html
    # Degradación: modal roto → confirm() nativo (la acción nunca corre
    # sin confirmación); avisos rotos → alert() nativo como última visibilidad
    assert 'window.confirm(' in html
    assert 'window.alert(' in html


# ── C3b: páginas de cliente migradas (carrito y catálogo) ──────────

def _sesion_cliente():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 11
        s['rol'] = 'cliente'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


def test_carrito_usa_el_componente():
    html = _sesion_cliente().get('/carrito').get_data(as_text=True)
    # Confirmaciones por modal (vaciar carrito; y el "ir al login" cuando
    # la sesión no es de cliente, que Jinja excluye de este render)
    assert 'await confirmarAccion(' in html
    # Avisos breves (tarjeta, RUC) y persistentes ("Entiendo")
    assert 'avisoAccion({' in html
    assert 'await avisoAccion({' in html
    # El alert duplicado del RUC quedó unificado en una sola función
    assert 'function validarRucFactura' in html


def test_carrito_redirige_despues_del_entiendo():
    # El aviso persistente no bloquea: la redirección al pedido aceptado
    # (o al checkout) ocurre DESPUÉS del await del "Entiendo"
    html = _sesion_cliente().get('/carrito').get_data(as_text=True)
    assert 'await avisoAccion({ mensaje: avisoExcluidos' in html
    assert "checkout_url" in html


def test_catalogo_usa_el_componente():
    html = app.test_client().get('/catalogo').get_data(as_text=True)
    assert 'await confirmarAccion(' in html   # comprar sin sesión → login
    assert 'avisoAccion({' in html           # producto no disponible → toast


def test_carrito_y_catalogo_sin_dialogos_nativos():
    # Las PÁGINAS ya no usan confirm()/alert() nativos (el componente sí,
    # como degradación fail-closed, pero vive en su propio archivo)
    for nombre in ('carrito.html', 'catalogo.html'):
        fuente = (_TEMPLATES / nombre).read_text(encoding='utf-8')
        assert 'confirm(' not in fuente, f'{nombre} aún usa confirm() nativo'
        assert 'alert(' not in fuente, f'{nombre} aún usa alert() nativo'
