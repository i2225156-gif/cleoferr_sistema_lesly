"""Columnas TIPO y RELACIONADO del listado de alertas.

Salian vacias ("–"): la consulta traia solo columnas de `alerta` y la
plantilla pedia `a.tipo` y `a.relacionado`, que no existen. Ahora TIPO
muestra el nombre del tipo (Stock bajo, Vencimiento) y RELACIONADO el
producto con enlace a su detalle.

Alertas y productos sinteticos dentro de una transaccion con rollback.
"""
import re
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


class ConnProxy:
    """commit() no-op: la ruta cree que comitea; el test hace rollback."""

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


def _sesion():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    """Un producto con stock bajo (alerta de tipo 1) y otro que vence
    pronto (alerta de tipo 3). Las alertas las crea la sincronizacion."""
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    nombre_stock = f"zzcol-stock-{_uuid.uuid4().hex[:8]}"
    nombre_venc = f"zzcol-venc-{_uuid.uuid4().hex[:8]}"
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo)
        VALUES (%s, 9.99, 3, TRUE) RETURNING id_producto
    """, (nombre_stock,))
    id_stock = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo, fecha_vencimiento)
        VALUES (%s, 9.99, 50, TRUE, CURRENT_DATE + INTERVAL '5 days')
        RETURNING id_producto
    """, (nombre_venc,))
    id_venc = cur.fetchone()['id_producto']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    monkeypatch.setenv('VENCIMIENTO_UMBRAL_DIAS', '30')
    r = _sesion().get('/alertas')
    assert r.status_code == 200
    alertas = {}
    for id_producto, tipo in ((id_stock, 1), (id_venc, 3)):
        cur.execute("""
            SELECT id_alerta FROM alerta
             WHERE id_producto = %s AND id_tipo_alerta = %s AND resuelta = FALSE
             ORDER BY id_alerta DESC LIMIT 1
        """, (id_producto, tipo))
        fila = cur.fetchone()
        assert fila, f'no se creo la alerta de tipo {tipo}'
        alertas[tipo] = fila['id_alerta']
    yield {'cur': cur, 'admin': _sesion(), 'id_stock': id_stock,
           'id_venc': id_venc, 'nombre_stock': nombre_stock,
           'nombre_venc': nombre_venc, 'alertas': alertas}
    proxy.rollback()
    raw.close()


def _html(fx):
    r = fx['admin'].get('/alertas')
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _fila(html, id_alerta):
    m = re.search(r'<tr data-id="%s">(.*?)</tr>' % id_alerta, html, re.DOTALL)
    assert m, f'la alerta {id_alerta} no aparece en el listado'
    return m.group(1)


def test_la_columna_tipo_muestra_el_nombre_del_tipo(fx):
    """TIPO muestra el nombre del tipo: Stock bajo y Vencimiento."""
    html = _html(fx)
    assert 'Stock bajo' in _fila(html, fx['alertas'][1])
    assert 'Vencimiento' in _fila(html, fx['alertas'][3])


def test_la_columna_relacionado_enlaza_al_detalle_del_producto(fx):
    """RELACIONADO muestra el producto y enlaza a su detalle."""
    html = _html(fx)
    fila_stock = _fila(html, fx['alertas'][1])
    assert fx['nombre_stock'] in fila_stock
    assert f'href="/productos/detalle/{fx["id_stock"]}"' in fila_stock
    fila_venc = _fila(html, fx['alertas'][3])
    assert fx['nombre_venc'] in fila_venc
    assert f'href="/productos/detalle/{fx["id_venc"]}"' in fila_venc


def test_las_columnas_no_salen_vacias(fx):
    """Ninguna de las dos columnas queda en «–» cuando hay producto y
    tipo conocidos."""
    html = _html(fx)
    for tipo in (1, 3):
        fila = _fila(html, fx['alertas'][tipo])
        assert '<td>–</td>' not in fila
