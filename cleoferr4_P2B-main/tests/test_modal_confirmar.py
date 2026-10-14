"""Componente global de diálogos (C3a): parcial incluido UNA vez en base.html.

Marcadores en el HTML renderizado (sin ejecutar JS): el modal de
confirmación viaja exactamente una vez por página, tanto en páginas de
cliente (base.html directo) como admin (admin_base hereda de base y no
lo duplica); la API global está declarada y el aviso de degradación
fail-closed nace VISIBLE en el HTML (el JS lo oculta al cargar: sin JS
el usuario ve la explicación, nunca un botón mudo).
"""
from app import app


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
