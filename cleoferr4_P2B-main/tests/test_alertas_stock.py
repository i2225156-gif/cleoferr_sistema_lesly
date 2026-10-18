"""Invariante de alertas de stock bajo, sin depender de datos vivos.

La sincronizacion entre producto.stock y la tabla alerta SOLO la
dispara GET /alertas (crear la alerta que falte por cada producto con
stock < 15 y resolver las de productos ya repuestos). Los tests montan
productos y alertas sinteticos dentro de una transaccion con rollback,
disparan la sincronizacion con esa misma conexion inyectada (el commit
de la ruta es no-op en el proxy, todo queda en la transaccion) y
verifican la invariante global: los datos reales tambien quedan
sincronizados dentro de la transaccion y se revierten al final.
"""
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest
from sqlalchemy import text

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


def _sesion(rol, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
    return c


@pytest.fixture()
def fx(monkeypatch):
    """3 productos bajo el umbral (3, 7, 14) y 1 repuesto (stock 50).

    Uno de los bajos ya tiene alerta sin resolver (la sync no debe
    duplicarla) y el repuesto tambien (stale: el caso que rompe la
    invariante si la sync no lo resuelve). Los otros dos bajos no
    tienen alerta: la sync debe crearlas.
    """
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    ids_bajos = []
    for stock in (3, 7, 14):
        cur.execute(
            "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 9.99, %s)"
            " RETURNING id_producto", (f"testal-{_uuid.uuid4().hex[:10]}", stock))
        ids_bajos.append(cur.fetchone()['id_producto'])
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 9.99, 50)"
        " RETURNING id_producto", (f"testal-{_uuid.uuid4().hex[:10]}",))
    id_repuesto = cur.fetchone()['id_producto']
    for idp in (ids_bajos[0], id_repuesto):
        cur.execute("""
            INSERT INTO alerta (id_tipo_alerta, id_producto, descripcion,
                               prioridad, fecha)
            VALUES (1, %s, 'test alertas stock', 'media', NOW())
        """, (idp,))
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'ids_bajos': ids_bajos,
           'id_repuesto': id_repuesto, 'vendedor': _sesion('vendedor', 8)}
    proxy.rollback()
    raw.close()


def test_alertas_count_coincide_con_productos_bajo_umbral(fx):
    """Tras la sincronizacion de GET /alertas, el contador de alertas
    coincide con los productos con stock bajo el umbral: una alerta sin
    resolver por producto (sin duplicar la preexistente) y la misma
    cifra global que la consulta del contador de /productos."""
    r = fx['vendedor'].get('/alertas')
    assert r.status_code == 200
    cur = fx['cur']
    # La sync creo las que faltaban: una por producto sintetico bajo
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM alerta
         WHERE resuelta = FALSE AND id_tipo_alerta = 1
           AND id_producto = ANY(%s)
    """, (fx['ids_bajos'],))
    assert cur.fetchone()['cnt'] == len(fx['ids_bajos'])
    # Invariante global (los datos reales tambien sincronizados en la tx)
    cur.execute("SELECT COUNT(*) AS cnt FROM producto WHERE stock < 15")
    bajos = cur.fetchone()['cnt']
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM alerta
         WHERE resuelta = FALSE AND id_tipo_alerta = 1
           AND id_producto IN (SELECT id_producto FROM producto
                                WHERE stock < 15)
    """)
    assert cur.fetchone()['cnt'] == bajos


def test_tras_sync_ninguna_alerta_activa_de_producto_repuesto(fx):
    """La sincronizacion de GET /alertas resuelve la alerta del producto
    repuesto (stock >= umbral): tras ella ninguna alerta sin resolver
    corresponde a un producto con stock >= 15."""
    r = fx['vendedor'].get('/alertas')
    assert r.status_code == 200
    cur = fx['cur']
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM alerta
         WHERE resuelta = FALSE AND id_tipo_alerta = 1
           AND id_producto NOT IN (SELECT id_producto FROM producto
                                    WHERE stock < 15)
    """)
    assert cur.fetchone()['cnt'] == 0
    # La alerta stale del repuesto sintetico quedo resuelta
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM alerta
         WHERE resuelta = TRUE AND id_tipo_alerta = 1
           AND id_producto = %s
    """, (fx['id_repuesto'],))
    assert cur.fetchone()['cnt'] == 1


def test_todas_las_alertas_sin_resolver_son_de_stock_bajo():
    """Invariante global de solo lectura: sin resolver solo hay alertas
    de stock bajo (id_tipo_alerta = 1)."""
    with db_supabase.engine_tienda.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM alerta"
                           " WHERE resuelta = FALSE AND id_tipo_alerta != 1")).scalar()
    assert n == 0
