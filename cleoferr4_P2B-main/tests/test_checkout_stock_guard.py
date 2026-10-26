"""C2: el checkout ya no puede sobrevenir stock (guarda en el UPDATE).

Contra la BD real dentro de transacciones con rollback: nada persiste.
El descuento pasa de `GREATEST(stock - cant, 0)` (que enmascaraba el
CHECK stock >= 0 y permitía vender dos veces la última unidad) a
`UPDATE ... WHERE stock >= cantidad RETURNING stock`:

- stock insuficiente en una línea → 409 con el NOMBRE del producto y
  cuánto queda, TODO el pedido revertido (la primera línea NO queda
  descontada) y sin detalles técnicos;
- borde N: comprar exactamente N de N tiene éxito y agota el stock;
- la fase previa de excluidos se mantiene (cantidad > stock se excluye
  con motivo y sin crear pedido);
- dos conexiones sobre el mismo producto real: el UPDATE con guarda
  serializa a los compradores (el segundo ESPERA el lock del primero,
  nunca ve un valor podrido y jamás baja de 0).
"""
import threading
import time
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


@pytest.fixture()
def fx(monkeypatch):
    """Cliente sintético + tres productos sintéticos en una sola TXN.
    Además expone dos productos REALES (el de menor stock y otro) para
    las pruebas de rollback: cuando la ruta revierte su transacción el
    proxy de tienda queda en blanco y los sintéticos desaparecen, pero
    los reales siguen visibles para verificar que NADA quedó descontado."""
    suf = _uuid.uuid4().hex[:10]
    raw_a = db_supabase.engine_auth.raw_connection()
    raw_t = db_supabase.engine_tienda.raw_connection()
    proxy_a, proxy_t = ConnProxy(raw_a), ConnProxy(raw_t)
    cur_a, cur_t = proxy_a.cursor(dictionary=True), proxy_t.cursor(dictionary=True)
    cur_a.execute("""
        INSERT INTO cliente (nombre, apellido, email, telefono, direccion,
                              contrasena, activo, id_tipo_documento, nro_documento)
        VALUES ('Guarda', 'Cliente Prueba', %s, '987654321', 'Calle Uno',
                'no-importa', TRUE, 1, %s)
        RETURNING id_cliente
    """, (f"guarda-{suf}@invalido", f"{_uuid.uuid4().int % 100000000:08d}"))
    id_cliente = cur_a.fetchone()['id_cliente']
    productos = {}
    for etiqueta, precio, stock in (('a', 10.00, 1), ('b', 20.00, 3), ('c', 30.00, 5)):
        cur_t.execute("""
            INSERT INTO producto (nombre, precio, stock, activo)
            VALUES (%s, %s, %s, TRUE) RETURNING id_producto, nombre
        """, (f"zzq-guarda-{etiqueta}-{suf}", precio, stock))
        fila = cur_t.fetchone()
        productos[etiqueta] = fila['id_producto']
        productos['nombre_' + etiqueta] = fila['nombre']
    # productos reales para las verificaciones tras el rollback de la ruta
    # (excluidos los sintéticos de este fixture, que tienen el stock menor)
    cur_t.execute("""SELECT id_producto, nombre, stock FROM producto
                      WHERE activo = TRUE AND stock >= 1
                        AND nombre NOT LIKE %s
                      ORDER BY stock ASC, id_producto LIMIT 2""",
                  (f"zzq-guarda-%",))
    reales = cur_t.fetchall()
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy_a)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy_t)
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = id_cliente
        s['rol'] = 'cliente'
        s['nombre'] = 'Guarda'
        s['_csrf_token'] = 'token-test'
    yield {'client': c, 'cur': cur_t, 'id_cliente': id_cliente, 'p': productos,
           'suf': suf, 'reales': reales}
    proxy_a.rollback()
    proxy_t.rollback()
    raw_a.close()
    raw_t.close()


def _checkout(fx, items, metodo='efectivo'):
    return fx['client'].post('/carrito/confirmar_v2', json={
        'items': items, 'metodo_pago': metodo,
        'tipo_comprobante': 'boleta_simple', 'csrf_token': 'token-test'})


def _stock(fx, id_producto):
    fx['cur'].execute("SELECT stock FROM producto WHERE id_producto = %s", (id_producto,))
    return fx['cur'].fetchone()['stock']


def _n_ventas(fx):
    fx['cur'].execute("SELECT COUNT(*) AS n FROM venta WHERE id_cliente = %s",
                     (fx['id_cliente'],))
    return fx['cur'].fetchone()['n']


# ── fase previa de excluidos se mantiene ────────────────────────────

def test_excluidos_previos_sin_stock_sigue_igual(fx):
    """Cantidad > stock: la fase previa de excluidos se mantiene igual
    (ok False cuando el carrito queda vacío, motivo con lo disponible)."""
    r = _checkout(fx, [{'id_producto': fx['p']['b'], 'cantidad': 99}])
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is False and 'Ningún producto' in d['error']
    assert len(d['excluidos']) == 1
    assert 'Stock insuficiente' in d['excluidos'][0]['motivo']
    assert 'quedan 3' in d['excluidos'][0]['motivo']
    assert _n_ventas(fx) == 0            # sin pedido: solo exclusiones
    assert _stock(fx, fx['p']['b']) == 3


# ── borde N: justo suficiente ───────────────────────────────────────

def test_borde_n_justo_suficiente_agota_y_cumple(fx):
    cur = fx['cur']
    r = _checkout(fx, [{'id_producto': fx['p']['b'], 'cantidad': 3}])
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is True
    id_venta = d['id_pedido']
    assert _stock(fx, fx['p']['b']) == 0
    cur.execute("""SELECT cantidad, precio_unitario FROM detalle_venta
                    WHERE id_venta = %s""", (id_venta,))
    det = cur.fetchone()
    assert det['cantidad'] == 3 and float(det['precio_unitario']) == 20.00
    cur.execute("""SELECT tipo, cantidad, stock_resultante FROM inventario_movimiento
                    WHERE observacion LIKE %s""", (f'Venta online #{id_venta}%',))
    mov = cur.fetchone()
    assert mov['tipo'] == 'salida' and mov['cantidad'] == 3 and mov['stock_resultante'] == 0
    cur.execute("SELECT estado, monto FROM pago WHERE id_venta = %s", (id_venta,))
    pago = cur.fetchone()
    assert pago['estado'] == 'pendiente' and float(pago['monto']) == 60.00


# ── la guarda del UPDATE: 0 filas = rechazo y rollback total ────────

def test_doble_linea_del_ultimo_producto_rechaza_todo(fx):
    """Dos líneas que agotan el stock de un producto REAL: antes el
    GREATEST dejaba pasar las dos (sobreventa silenciosa); ahora la
    segunda línea no encuentra stock, el pedido se revierte ENTERO y
    nada queda escrito (verificado sobre datos comprometidos)."""
    if len(fx['reales']) < 1:
        pytest.skip('sin productos reales con stock')
    cur = fx['cur']
    prod = fx['reales'][0]
    pid, nombre, stock = prod['id_producto'], prod['nombre'], prod['stock']
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE id_producto = %s",
                (pid,))
    mov_antes = cur.fetchone()['n']
    r = _checkout(fx, [{'id_producto': pid, 'cantidad': stock},
                       {'id_producto': pid, 'cantidad': stock}])
    assert r.status_code == 409, r.get_json()
    d = r.get_json()
    assert d['ok'] is False
    # mensaje con el NOMBRE del producto y lo disponible, sin tecnicismos
    assert nombre in d['error']
    assert 'quedan 0' in d['error']
    for tecnico in ('UPDATE', 'SELECT', 'psycopg2', 'Traceback', 'ERROR:'):
        assert tecnico not in d['error']
    # rollback TOTAL: sin venta para el cliente, sin movimiento nuevo y
    # el stock real intacto (lectura sobre datos comprometidos)
    assert _n_ventas(fx) == 0
    cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (pid,))
    assert cur.fetchone()['stock'] == stock
    cur.execute("SELECT COUNT(*) AS n FROM inventario_movimiento WHERE id_producto = %s",
                (pid,))
    assert cur.fetchone()['n'] == mov_antes


def test_multi_producto_segunda_linea_falla_y_la_primera_se_deshace(fx):
    """Carrito REAL [P1 x1, P2 xS, P2 xS]: en la TXN bajan las primeras
    líneas, pero la segunda línea de P2 no encuentra stock → TODO se
    revierte: P1 y P2 quedan con su stock intacto y sin pedido."""
    if len(fx['reales']) < 2:
        pytest.skip('sin dos productos reales con stock')
    cur = fx['cur']
    p1, p2 = fx['reales'][0], fx['reales'][1]
    r = _checkout(fx, [{'id_producto': p1['id_producto'], 'cantidad': 1},
                       {'id_producto': p2['id_producto'], 'cantidad': p2['stock']},
                       {'id_producto': p2['id_producto'], 'cantidad': p2['stock']}])
    assert r.status_code == 409, r.get_json()
    d = r.get_json()
    assert d['ok'] is False
    # el producto nombrado es el que falló (P2), no el que ya había bajado
    assert p2['nombre'] in d['error']
    assert _n_ventas(fx) == 0
    cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (p1['id_producto'],))
    assert cur.fetchone()['stock'] == p1['stock'], 'la primera línea NO debe quedar descontada'
    cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (p2['id_producto'],))
    assert cur.fetchone()['stock'] == p2['stock']


def test_producto_no_disponible_mensaje_de_negocio(fx):
    """Producto desactivado: la respuesta es de NEGOCIO (excluido con
    motivo claro), sin detalles técnicos ni 500 genérico."""
    cur = fx['cur']
    cur.execute("UPDATE producto SET activo = FALSE WHERE id_producto = %s",
                (fx['p']['c'],))
    r = _checkout(fx, [{'id_producto': fx['p']['c'], 'cantidad': 1}])
    d = r.get_json()
    assert d['ok'] is False and 'disponible' in d['error']
    for tecnico in ('UPDATE', 'psycopg2', 'Traceback'):
        assert tecnico not in d['error']


# ── dos conexiones sobre el mismo producto real ──────────────────────

SQL_GUARDA = """
    UPDATE producto
       SET stock = stock - %s
     WHERE id_producto = %s AND stock >= %s
     RETURNING stock
"""


def test_dos_conexiones_serializan_y_nunca_sobrevenen():
    """Réplica del SQL exacto de la ruta con dos conexiones reales.

    Ambos compradores leen el mismo stock en el pre-check (la ventana de
    carrera existe), pero el UPDATE con guarda serializa: B ESPERA el
    lock de A y no lee un valor a medias. Como A termina en rollback,
    B encuentra las unidades de vuelta; en producción, con el pedido de A
    confirmado, B recibiría 0 filas → ValueError → pedido revertido.
    Nada persiste (ambas conexiones hacen rollback)."""
    raw1 = db_supabase.engine_tienda.raw_connection()
    raw2 = db_supabase.engine_tienda.raw_connection()
    c1 = raw1.cursor()
    c1.execute("""SELECT id_producto, stock FROM producto
                  WHERE activo = TRUE AND stock >= 1
                  ORDER BY stock ASC, id_producto LIMIT 1""")
    fila = c1.fetchone()
    raw1.rollback()
    if not fila:
        pytest.skip('no hay productos activos con stock')
    pid, stock = fila[0], fila[1]

    resultados = {}
    a_actualizo = threading.Event()
    b_va_a_intentarlo = threading.Event()

    def comprador_a():
        cur = raw1.cursor()
        log = {}
        try:
            cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (pid,))
            log['precheck'] = cur.fetchone()[0]
            t0 = time.perf_counter()
            cur.execute(SQL_GUARDA, (stock, pid, stock))
            fila_upd = cur.fetchone()
            log['update'] = fila_upd[0] if fila_upd else None
            log['espera_s'] = time.perf_counter() - t0
            if fila_upd is not None:
                cur.execute("""
                    INSERT INTO venta (id_tipo_venta, id_estado_venta, num_operacion)
                    VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                            (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
                            %s)
                """, (f"c2-A-{_uuid.uuid4().hex[:8]}",))
                cur.execute("INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)"
                            " VALUES (CURRVAL('venta_id_venta_seq'), %s, %s, 10.00)",
                            (pid, stock))
        except Exception as e:
            log['error'] = repr(e)
        finally:
            # retener el lock hasta que B haya marcado su intento (B queda
            # bloqueado en el UPDATE) y luego deshacer todo
            a_actualizo.set()
            b_va_a_intentarlo.wait(timeout=10)
            time.sleep(0.4)
            raw1.rollback()
            log['rollback'] = True
        resultados['A'] = log

    def comprador_b():
        cur = raw2.cursor()
        log = {}
        try:
            a_actualizo.wait(timeout=10)
            cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (pid,))
            log['precheck'] = cur.fetchone()[0]
            b_va_a_intentarlo.set()          # A sabe que B ya va a actualizar
            t0 = time.perf_counter()
            cur.execute(SQL_GUARDA, (stock, pid, stock))
            fila_upd = cur.fetchone()
            log['update'] = fila_upd[0] if fila_upd else None
            log['espera_s'] = time.perf_counter() - t0
        except Exception as e:
            log['error'] = repr(e)
        finally:
            raw2.rollback()
            log['rollback'] = True
        resultados['B'] = log

    t_a = threading.Thread(target=comprador_a)
    t_b = threading.Thread(target=comprador_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=30)
    t_b.join(timeout=30)

    A, B = resultados.get('A'), resultados.get('B')
    assert A is not None and B is not None
    # la ventana de carrera existió: ambos pre-checks vieron el stock completo
    assert A['precheck'] == stock and B['precheck'] == stock
    # A tomó las unidades y B esperó el lock (no leyó un valor a medias)
    assert A['update'] == 0
    assert B['update'] is not None and B['update'] >= 0
    assert B['espera_s'] >= 0.3, f"B no esperó el lock de A (espera={B['espera_s']:.3f}s)"
    # nunca negativo
    assert A['update'] >= 0 and B['update'] >= 0

    # la guarda en sí: pedir stock+1 no encuentra fila (lo que la ruta
    # traduce a ValueError → 409)
    raw3 = db_supabase.engine_tienda.raw_connection()
    c3 = raw3.cursor()
    c3.execute(SQL_GUARDA, (stock + 1, pid, stock + 1))
    assert c3.fetchone() is None
    raw3.rollback()
    raw3.close()

    # el producto real quedó intacto
    raw4 = db_supabase.engine_tienda.raw_connection()
    c4 = raw4.cursor()
    c4.execute("SELECT stock FROM producto WHERE id_producto = %s", (pid,))
    assert c4.fetchone()[0] == stock
    raw4.rollback()
    raw4.close()
    raw1.close()
    raw2.close()
