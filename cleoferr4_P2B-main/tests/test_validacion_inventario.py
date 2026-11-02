"""FASE 2 — validaciones de servidor del form de INVENTARIO.

Casos límite por campo: tipo no válido (antes: flash crudo de la BD),
cantidad no entera (antes: 500 por int() sin capturar), cantidad 0 o
negativa, precio unitario no numérico o negativo, producto/proveedor
inexistente, y los caminos felices entrada/salida con stock actualizado
(dentro del rollback).

El CHECK de precio_unitario >= 0 va en db/10_inventario_precio_check.sql
(SIN ejecutar): el rechazo en servidor ya lo impide antes de la BD.
"""
import threading
import uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app

MARCA = 'zzqf2inv'


class ConnProxy:
    """Contrato de conexión proxied con rollback (como P1..P4)."""

    def __init__(self, raw):
        self._raw = raw
        self._savepoint = None
        self._seq = 0

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            return self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self._raw.cursor()

    def begin(self):
        self._seq += 1
        self._savepoint = f'peticion_inventario_{self._seq}'
        self.cursor().execute(f'SAVEPOINT {self._savepoint}')

    def commit(self):
        if self._savepoint:
            self.cursor().execute(f'RELEASE SAVEPOINT {self._savepoint}')
            self._savepoint = None

    def rollback(self):
        if self._savepoint:
            self.cursor().execute(f'ROLLBACK TO SAVEPOINT {self._savepoint}')
            self.cursor().execute(f'RELEASE SAVEPOINT {self._savepoint}')
            self._savepoint = None
        else:
            self._raw.rollback()

    def close(self):
        pass


def _vendedor():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(f"""
        INSERT INTO producto (nombre, precio, stock)
        VALUES ('{MARCA}-tornillo', 0.50, 50)
        RETURNING id_producto
    """)
    pid = cur.fetchone()['id_producto']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    monkeypatch.setattr(modulo_app, '_permisos_staff', lambda: {'inventario'})
    yield {'c': _vendedor(), 'cur': cur, 'pid': pid, 'proxy': proxy}
    raw.rollback()
    raw.close()


def _registrar(fx, **campos):
    fx['proxy'].begin()
    datos = {'tipo': 'entrada', 'id_producto': str(fx['pid']),
             'cantidad': '3', 'precio_unitario': '0.50',
             'observacion': 'compra de prueba', 'id_proveedor': '',
             'id_vendedor': ''}
    datos.update(campos)
    return fx['c'].post('/inventario/registrar', data=datos,
                        follow_redirects=True).get_data(as_text=True)


def _stock(fx):
    fx['cur'].execute("SELECT stock FROM producto WHERE id_producto = %s",
                      (fx['pid'],))
    return fx['cur'].fetchone()['stock']


def _movimientos(fx):
    fx['cur'].execute("SELECT COUNT(*) AS n FROM inventario_movimiento"
                      " WHERE id_producto = %s", (fx['pid'],))
    return fx['cur'].fetchone()['n']


# ── tipo ───────────────────────────────────────────────────────

def test_tipo_invalido_flash_claro_no_crudo(fx):
    """Antes: 'XYZ' llegaba a la BD y el flash mostraba el texto del
    error de Postgres. Ahora: mensaje claro y sin movimiento."""
    html = _registrar(fx, tipo='XYZ')
    assert 'El tipo de movimiento debe ser entrada o salida.' in html
    assert 'violates' not in html and 'pg_' not in html
    assert _movimientos(fx) == 0


# ── cantidad ────────────────────────────────────────────────────

def test_cantidad_no_numerica_sin_500(fx):
    """Antes: int('abc') reventaba con 500. Ahora: flash claro."""
    fx['proxy'].begin()
    r = fx['c'].post('/inventario/registrar',
                     data={'tipo': 'entrada', 'id_producto': str(fx['pid']),
                           'cantidad': 'abc'},
                     follow_redirects=True)
    assert r.status_code == 200
    assert 'El campo «cantidad» debe ser un número entero' in r.get_data(as_text=True)
    assert _stock(fx) == 50


def test_cantidad_cero_rechazada(fx):
    html = _registrar(fx, cantidad='0')
    assert 'El campo «cantidad» debe ser mayor que cero' in html
    assert _stock(fx) == 50


def test_cantidad_negativa_rechazada(fx):
    """Antes una 'salida' con -5 AUMENTABA el stock. Ahora ni entra."""
    html = _registrar(fx, tipo='salida', cantidad='-5')
    assert 'El campo «cantidad» no puede ser negativo' in html
    assert _stock(fx) == 50


# ── precio unitario ─────────────────────────────────────────────

def test_precio_unitario_no_numerico_rechazado(fx):
    html = _registrar(fx, precio_unitario='abc')
    assert 'El campo «precio unitario» debe ser un número' in html
    assert _movimientos(fx) == 0


def test_precio_unitario_negativo_rechazado(fx):
    """El CHECK de la BD (db/10, sin ejecutar aún) es la última barrera;
    el servidor ya lo rechaza antes."""
    html = _registrar(fx, precio_unitario='-3')
    assert 'El campo «precio unitario» no puede ser negativo' in html
    assert _movimientos(fx) == 0


def test_precio_unitario_vacio_queda_null(fx):
    html = _registrar(fx, precio_unitario='')
    assert 'registrado' in html
    fx['cur'].execute("SELECT precio_unitario FROM inventario_movimiento"
                      " WHERE id_producto = %s", (fx['pid'],))
    assert fx['cur'].fetchone()['precio_unitario'] is None


# ── producto y proveedor ────────────────────────────────────────

def test_producto_no_numerico_rechazado(fx):
    html = _registrar(fx, id_producto='abc')
    assert 'El campo «producto» debe ser un número entero' in html


def test_producto_inexistente_rechazado(fx):
    html = _registrar(fx, id_producto='999999')
    assert 'Producto no encontrado.' in html


def test_proveedor_inexistente_rechazado(fx):
    html = _registrar(fx, id_proveedor='999999')
    assert 'El proveedor seleccionado no existe.' in html
    assert _movimientos(fx) == 0


# ── caminos felices (dentro del rollback) ───────────────────────

def test_entrada_actualiza_stock(fx):
    html = _registrar(fx, cantidad='3', observacion='   ingresa lote   ')
    assert 'Nuevo stock: 53' in html
    assert _stock(fx) == 53
    fx['cur'].execute("SELECT observacion FROM inventario_movimiento"
                      " WHERE id_producto = %s", (fx['pid'],))
    assert fx['cur'].fetchone()['observacion'] == 'ingresa lote'


def test_salida_actualiza_stock_y_respecta_limite(fx):
    html = _registrar(fx, tipo='salida', cantidad='10')
    assert 'Nuevo stock: 40' in html
    assert _stock(fx) == 40
    fx['cur'].execute("SELECT stock_resultante FROM inventario_movimiento"
                      " WHERE id_producto = %s", (fx['pid'],))
    assert fx['cur'].fetchone()['stock_resultante'] == 40
    html = _registrar(fx, tipo='salida', cantidad='999')
    assert 'Stock insuficiente. Disponible: 40' in html
    assert _stock(fx) == 40      # no cambió
    assert _movimientos(fx) == 1


# ── concurrencia real sobre un producto sintético ───────────────────

class CursorBarrera:
    """Obliga a ambas peticiones a leer el stock antes de actualizar."""

    def __init__(self, cur, barrera):
        self._cur = cur
        self._barrera = barrera
        self._sincronizar = False

    def execute(self, sql, params=None):
        self._sincronizar = 'SELECT stock FROM producto' in sql
        return self._cur.execute(sql, params)

    def fetchone(self):
        fila = self._cur.fetchone()
        if self._sincronizar:
            self._sincronizar = False
            self._barrera.wait(timeout=15)
        return fila

    def __getattr__(self, nombre):
        return getattr(self._cur, nombre)


class ConnConcurrente(ConnProxy):
    def __init__(self, raw, barrera):
        super().__init__(raw)
        self._barrera = barrera

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cur = self._raw.cursor()
        return CursorBarrera(cur, self._barrera)

    def commit(self):
        self._raw.commit()


@pytest.fixture()
def producto_concurrente():
    """Producto visible para dos conexiones; se elimina sin dejar residuos."""
    pids = []

    def crear(stock):
        raw = db_supabase.engine_tienda.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("""
                INSERT INTO producto (nombre, precio, stock)
                VALUES (%s, 1.00, %s) RETURNING id_producto
            """, (f"{MARCA}-conc-{uuid.uuid4().hex}", stock))
            pid = cur.fetchone()[0]
            raw.commit()
            pids.append(pid)
            return pid
        finally:
            raw.close()

    yield crear

    raw = db_supabase.engine_tienda.raw_connection()
    try:
        cur = raw.cursor()
        for pid in pids:
            cur.execute("DELETE FROM inventario_movimiento WHERE id_producto = %s", (pid,))
            cur.execute("DELETE FROM producto WHERE id_producto = %s", (pid,))
        raw.commit()
    finally:
        raw.rollback()
        raw.close()


def _dos_movimientos_simultaneos(monkeypatch, pid, tipo, cantidad):
    barrera = threading.Barrier(2)
    local = threading.local()
    raws = []
    resultados = {}
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: local.conn)

    def trabajador(nombre):
        raw = db_supabase.engine_tienda.raw_connection()
        raws.append(raw)
        local.conn = ConnConcurrente(raw, barrera)
        try:
            cliente = _vendedor()
            with cliente.session_transaction() as sesion:
                sesion['rol'] = 'administrador'
            respuesta = cliente.post('/inventario/registrar', data={
                'tipo': tipo, 'id_producto': str(pid), 'cantidad': str(cantidad),
                'precio_unitario': '', 'observacion': f'{MARCA}-{nombre}',
                'id_proveedor': '', 'id_vendedor': ''})
            with cliente.session_transaction() as sesion:
                mensajes = sesion.get('_flashes', [])
            resultados[nombre] = (respuesta.status_code, mensajes)
        except Exception as exc:
            resultados[nombre] = repr(exc)

    hilos = [threading.Thread(target=trabajador, args=(nombre,)) for nombre in ('A', 'B')]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    assert all(not hilo.is_alive() for hilo in hilos)
    for raw in raws:
        raw.rollback()
        raw.close()
    assert sorted(valor[0] for valor in resultados.values()) == [302, 302], resultados

    raw = db_supabase.engine_tienda.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("SELECT stock FROM producto WHERE id_producto = %s", (pid,))
        stock = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM inventario_movimiento WHERE id_producto = %s", (pid,))
        movimientos = cur.fetchone()[0]
        return stock, movimientos
    finally:
        raw.rollback()
        raw.close()


def test_dos_salidas_simultaneas_solo_procede_una(monkeypatch, producto_concurrente):
    pid = producto_concurrente(10)
    stock, movimientos = _dos_movimientos_simultaneos(monkeypatch, pid, 'salida', 6)
    assert movimientos == 1
    assert stock == 4
    assert stock >= 0


def test_dos_entradas_simultaneas_suman_ambas(monkeypatch, producto_concurrente):
    pid = producto_concurrente(10)
    stock, movimientos = _dos_movimientos_simultaneos(monkeypatch, pid, 'entrada', 5)
    assert movimientos == 2
    assert stock == 20
