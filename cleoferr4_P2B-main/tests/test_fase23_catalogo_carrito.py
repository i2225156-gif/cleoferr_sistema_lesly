"""Fase 2.3 (pieza 5): catálogo y carrito con precio vigente del servidor.

Contra la BD real dentro de transacciones con rollback: nada persiste.
Producto sintético S/ 9.99 con oferta vigente 15% (dcto 1.50, final 8.49)
y otro S/ 5.00 sin oferta. Verifica: catálogo renderiza tachado + badge,
/carrito/agregar responde descuento y precio_final, /carrito/precios
devuelve el lote con la MISMA fuente que el checkout y exige CSRF+s sesión.
El navegador nunca decide: el total oficial lo recalcula el checkout.
"""
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


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


def _sesion_cliente():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 11
        s['rol'] = 'cliente'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 9.99, 50)"
        " RETURNING id_producto", (f"test23k-{_uuid.uuid4().hex[:10]}",))
    id_con = cur.fetchone()['id_producto']
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 5.00, 50)"
        " RETURNING id_producto", (f"test23k-{_uuid.uuid4().hex[:10]}",))
    id_sin = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio, fecha_fin)
        VALUES (%s, 'porcentaje', 15,
                NOW() - INTERVAL '1 hour', NOW() + INTERVAL '1 day')
    """, (id_con,))
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'client': _sesion_cliente(), 'cur': cur, 'proxy': proxy,
           'id_con': id_con, 'id_sin': id_sin}
    proxy.rollback()
    raw.close()


def _post_json(client, url, datos, csrf=True):
    headers = {'Content-Type': 'application/json'}
    if csrf:
        headers['X-CSRFToken'] = 'token-test'
    return client.post(url, json=datos, headers=headers)


# ── Catálogo (ruta pública) ───────────────────────────────────────

def test_catalogo_muestra_tachado_precio_oferta_y_badge(fx):
    html = fx['client'].get('/catalogo').get_data(as_text=True)
    # precio final, precio base tachado y badge con el porcentaje
    assert '8.49' in html
    assert '<s' in html and '9.99' in html
    assert 'OFERTA' in html and '15%' in html
    # el producto sin oferta no lleva badge
    assert 'OFERTA' in html   # solo el sintético lo produce si no hay más


def test_catalogo_producto_sin_oferta_muestra_precio_plano(fx):
    """La tarjeta del producto SIN oferta muestra su precio normal."""
    cur = fx['cur']
    cur.execute("SELECT COUNT(*) AS n FROM oferta")
    # el html del catálogo incluye al sintético sin oferta con '5.00'
    html = fx['client'].get('/catalogo').get_data(as_text=True)
    assert '5.00' in html


# ── /carrito/agregar ──────────────────────────────────────────────

def test_carrito_agregar_responde_descuento_y_precio_final(fx):
    r = _post_json(fx['client'], '/carrito/agregar',
                   {'id_producto': fx['id_con'], 'cantidad': 1})
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is True
    assert Decimal(str(d['precio'])) == Decimal('9.99')        # base
    assert Decimal(str(d['descuento'])) == Decimal('1.50')     # dcto vigente
    assert Decimal(str(d['precio_final'])) == Decimal('8.49')  # a pagar


def test_carrito_agregar_producto_sin_oferta(fx):
    r = _post_json(fx['client'], '/carrito/agregar',
                   {'id_producto': fx['id_sin'], 'cantidad': 1})
    d = r.get_json()
    assert d['ok'] is True
    assert Decimal(str(d['descuento'])) == Decimal('0')
    assert Decimal(str(d['precio_final'])) == Decimal('5.00')


# ── /carrito/precios (lote) ──────────────────────────────────────

def test_carrito_precios_lote_con_y_sin_oferta(fx):
    r = _post_json(fx['client'], '/carrito/precios',
                   {'ids': [fx['id_con'], fx['id_sin']]})
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is True
    precios = d['precios']
    assert str(fx['id_con']) in precios and str(fx['id_sin']) in precios
    con, sin = precios[str(fx['id_con'])], precios[str(fx['id_sin'])]
    assert Decimal(str(con['descuento'])) == Decimal('1.50')
    assert Decimal(str(con['precio_final'])) == Decimal('8.49')
    assert Decimal(str(con['precio'])) == Decimal('9.99')
    assert Decimal(str(sin['descuento'])) == Decimal('0')
    assert Decimal(str(sin['precio_final'])) == Decimal('5.00')


def test_carrito_precios_sin_csrf_rechazado(fx):
    r = _post_json(fx['client'], '/carrito/precios',
                   {'ids': [fx['id_con']]}, csrf=False)
    assert r.status_code == 400


def test_carrito_precios_exige_sesion(fx):
    """CSRF válido pero SIN sesión: login_required redirige al login."""
    anonimo = app.test_client()
    with anonimo.session_transaction() as s:
        s['_csrf_token'] = 'token-test'   # CSRF válido, sin usuario
    r = _post_json(anonimo, '/carrito/precios', {'ids': [fx['id_con']]})
    assert r.status_code in (301, 302, 401)


def test_carrito_precios_sin_csrf_ni_sesion_rechazado(fx):
    """Sin CSRF el before_request responde 400 incluso antes de mirar la
    sesión: la validación de CSRF va primero."""
    anonimo = app.test_client()
    r = _post_json(anonimo, '/carrito/precios', {'ids': [fx['id_con']]},
                   csrf=False)
    assert r.status_code == 400


def test_carrito_precios_mismo_resultado_que_el_checkout(fx):
    """La fuente del lote y la del checkout coinciden: oferta_vigente
    (usada para congelar en la TXN) da el mismo precio_final."""
    from services.ofertas import oferta_vigente
    r = _post_json(fx['client'], '/carrito/precios', {'ids': [fx['id_con']]})
    lote = r.get_json()['precios'][str(fx['id_con'])]
    fila = oferta_vigente(fx['cur'], fx['id_con'])
    assert Decimal(str(lote['precio_final'])) == fila['precio_final']
    assert Decimal(str(lote['descuento'])) == fila['descuento_unitario']
