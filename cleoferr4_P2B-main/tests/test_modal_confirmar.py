"""Componente global de diálogos (C3a..C3c): parcial incluido UNA vez en base.html.

C3a: marcadores en el HTML renderizado (sin ejecutar JS): el modal viaja
exactamente una vez por página (cliente y admin), la API global está
declarada y el aviso de degradación fail-closed nace VISIBLE (el JS lo
oculta al cargar).

C3b: las páginas de cliente (carrito, catálogo) llaman a la API del
componente (confirmarAccion/avisoAccion) y sus fuentes ya no contienen
confirm()/alert() nativos (el propio componente sí, como degradación).

C3c: las páginas admin migran sus formularios POST a data-confirm
(alertas resuelve directo con toast; producto_form avisa por toast).
C3d convierte los borrados de producto/cliente a form POST con CSRF
(ya sin data-href). El GET /alertas ESCRIBE la sincronización: se
prueba con conexión proxied y rollback (nada persiste).
"""
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest
import uuid as _uuid

import db_supabase
import app as modulo_app
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


# ── C3c: páginas admin migradas ────────────────────────────────────

_ARCHIVOS_C3C = ('caja.html', 'admin_reservas.html', 'admin_ofertas.html',
                 'clientes.html', 'proveedores.html', 'productos.html',
                 'alertas.html', 'producto_form.html')


def test_paginas_admin_c3c_sin_dialogos_nativos():
    for nombre in _ARCHIVOS_C3C:
        fuente = (_TEMPLATES / nombre).read_text(encoding='utf-8')
        assert 'confirm(' not in fuente, f'{nombre} aún usa confirm() nativo'
        assert 'alert(' not in fuente, f'{nombre} aún usa alert() nativo'


def test_forms_destruccion_con_data_confirm_peligro():
    # Cerrar caja, liberar reservas y eliminar proveedor: tipo peligro
    for nombre, mensaje in (('caja.html', '¿Confirmar cierre de caja #'),
                            ('admin_reservas.html', '¿Liberar'),
                            ('proveedores.html', '¿Confirma eliminar este proveedor?')):
        fuente = (_TEMPLATES / nombre).read_text(encoding='utf-8')
        assert 'data-confirm="' in fuente, nombre
        assert 'data-confirm-tipo="peligro"' in fuente, nombre
        assert mensaje in fuente, nombre


def test_formularios_de_borrado_post_con_data_confirm():
    # C3d: los borrados de producto/cliente son form POST con data-confirm
    # peligro (el enlace data-href de C3c fue sustituido por el formulario)
    for nombre, mensaje in (('clientes.html', '¿Eliminar este cliente?'),
                            ('productos.html', '¿Eliminar este producto?')):
        fuente = (_TEMPLATES / nombre).read_text(encoding='utf-8')
        assert 'method="POST" action=' in fuente, nombre
        assert f'data-confirm="{mensaje}"' in fuente, nombre
        assert 'data-confirm-tipo="peligro"' in fuente, nombre
        assert 'data-href' not in fuente, nombre


def test_admin_render_con_componente():
    c = _sesion_admin()
    # Productos: enlace eliminar con data-confirm (hay filas reales)
    html = c.get('/productos').get_data(as_text=True)
    assert 'data-confirm="¿Eliminar este producto?"' in html
    # Clientes: ídem
    html = c.get('/clientes').get_data(as_text=True)
    assert 'data-confirm="¿Eliminar este cliente?"' in html
    # producto_form: aviso de imagen > 5 MB por toast (campo se limpia igual)
    html = c.get('/productos/nuevo').get_data(as_text=True)
    assert "avisoAccion({ mensaje: 'La imagen supera los 5 MB.'" in html
    assert "this.value = ''" in html


class _Proxy:
    """Contrato de conexión proxied con rollback (como test_admin_layout)."""

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


@pytest.fixture()
def fx_tienda(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = _Proxy(raw)
    cur = proxy.cursor(dictionary=True)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'admin': _sesion_admin()}
    proxy.rollback()
    raw.close()


def test_admin_ofertas_render_con_componente(fx_tienda):
    # Ofertas sintéticas dentro de la txn con rollback (una activa y una
    # inactiva): garantizan AMBOS formularios. Con datos vivos la rama
    # "Activar"/"Desactivar" depende de cómo esté la única oferta real.
    cur = fx_tienda['cur']
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 9.99, 5)"
        " RETURNING id_producto", (f"test-c3c-{_uuid.uuid4().hex[:8]}",))
    pid = cur.fetchone()['id_producto']
    cur.execute(
        "INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio, fecha_fin, activa)"
        " VALUES (%s, 'porcentaje', 10, NOW() - INTERVAL '1 hour',"
        " NOW() + INTERVAL '2 hours', TRUE)", (pid,))
    cur.execute(
        "INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio, fecha_fin, activa)"
        " VALUES (%s, 'porcentaje', 15, NOW() - INTERVAL '1 hour',"
        " NOW() + INTERVAL '2 hours', FALSE)", (pid,))
    html = fx_tienda['admin'].get('/admin/ofertas').get_data(as_text=True)
    assert 'data-confirm="¿Desactivar esta oferta?' in html
    assert 'data-confirm="¿Activar esta oferta?' in html


def test_alertas_resuelve_directo_con_toast(fx_tienda):
    # Confirm eliminado (decisión aprobada): resuelve directo y el
    # resultado se informa con el toast propio (absorbido en C3e).
    html = fx_tienda['admin'].get('/alertas').get_data(as_text=True)
    assert 'resolverAlertaAjax' in html
    assert '¿Marcar esta alerta como resuelta?' not in html
    assert 'showToast(' in html
