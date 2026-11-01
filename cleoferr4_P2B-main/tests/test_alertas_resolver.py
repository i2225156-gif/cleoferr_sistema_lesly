"""Resolver una alerta desde /alertas: id real, JSON para AJAX y CSRF.

Reproduce el bug reportado: la plantilla usaba `a.id` (la columna real es
id_alerta), asi que el formulario y el fetch iban a /alertas/resolver/
(sin id: 404) y ademas el fetch esperaba JSON mientras la ruta respondia
con una redireccion. Camino feliz contra la BD real dentro de una
transaccion con rollback: nada persiste.
"""
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


class ConnProxy:
    """commit() no-op: la ruta cree que commitea; el test hace rollback."""

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


def _sesion(rol, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    """Una alerta de stock (tipo 1) y una de vencimiento (tipo 3).

    La de vencimiento va sobre un producto con fecha_vencimiento dentro
    del umbral: si no, la sincronizacion de GET /alertas la resolveria
    como stale y no apareceria en el listado.
    """
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)

    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo)
        VALUES (%s, 9.99, 3, TRUE) RETURNING id_producto
    """, (f"testaler-{_uuid.uuid4().hex[:10]}",))
    id_prod_stock = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO alerta (id_tipo_alerta, id_producto, descripcion,
                            prioridad, fecha)
        VALUES (1, %s, 'Stock insuficiente (test)', 'alta', NOW())
        RETURNING id_alerta
    """, (id_prod_stock,))
    id_alerta_stock = cur.fetchone()['id_alerta']

    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo, fecha_vencimiento)
        VALUES (%s, 9.99, 50, TRUE, CURRENT_DATE + INTERVAL '5 days')
        RETURNING id_producto
    """, (f"testaler-{_uuid.uuid4().hex[:10]}",))
    id_prod_venc = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO alerta (id_tipo_alerta, id_producto, descripcion,
                            prioridad, fecha)
        VALUES (3, %s, 'Vence pronto (test)', 'media', NOW())
        RETURNING id_alerta
    """, (id_prod_venc,))
    id_alerta_venc = cur.fetchone()['id_alerta']

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'admin': _sesion('administrador', 7),
           'id_alerta_stock': id_alerta_stock,
           'id_alerta_venc': id_alerta_venc}
    proxy.rollback()
    raw.close()


def _resuelta(fx, id_alerta):
    fx['cur'].execute('SELECT resuelta FROM alerta WHERE id_alerta = %s',
                      (id_alerta,))
    return fx['cur'].fetchone()['resuelta']


def test_cada_accion_de_la_pagina_lleva_el_id_real_de_su_alerta(fx):
    """El boton, el formulario y el fetch usan id_alerta (no a.id, que no
    existe: la columna real es id_alerta) y la fila lleva data-id para
    poder ocultarse tras resolver."""
    r = fx['admin'].get('/alertas')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for id_alerta in (fx['id_alerta_stock'], fx['id_alerta_venc']):
        assert f'action="/alertas/resolver/{id_alerta}"' in html
        assert f'<tr data-id="{id_alerta}">' in html
        assert f'resolverAlertaAjax({id_alerta})' in html
    # ninguna accion sin id (era el 404)
    assert 'action="/alertas/resolver/"' not in html
    assert 'resolverAlertaAjax()' not in html


def test_el_post_ajax_devuelve_json_y_resuelve_la_alerta(fx):
    """El fetch de la pagina espera JSON: la ruta responde JSON y la
    alerta queda resuelta de verdad en la BD."""
    r = fx['admin'].post(f'/alertas/resolver/{fx["id_alerta_stock"]}', headers={
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': 'token-test',
        'Accept': 'application/json',
    })
    assert r.status_code == 200
    assert r.is_json
    assert r.get_json()['ok'] is True
    assert _resuelta(fx, fx['id_alerta_stock']) is True


def test_el_post_de_un_formulario_normal_redirige_a_alertas(fx):
    """Sin AJAX: el boton del formulario sigue siendo un POST normal con
    flash y redireccion a /alertas."""
    r = fx['admin'].post(f'/alertas/resolver/{fx["id_alerta_venc"]}',
                         data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    assert '/alertas' in r.headers['Location']
    assert _resuelta(fx, fx['id_alerta_venc']) is True


def test_el_post_ajax_sin_token_csrf_no_resuelve_nada(fx):
    """El POST es de escritura: sin token CSRF no resuelve (400 JSON, no
    una pagina HTML)."""
    r = fx['admin'].post(f'/alertas/resolver/{fx["id_alerta_stock"]}',
                         headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 400
    assert r.is_json
    assert r.get_json()['ok'] is False
    assert _resuelta(fx, fx['id_alerta_stock']) is False


def test_el_fetch_de_la_plantilla_manda_el_token_csrf(fx):
    html = fx['admin'].get('/alertas').get_data(as_text=True)
    assert 'X-CSRFToken' in html
    assert '"token-test"' in html
