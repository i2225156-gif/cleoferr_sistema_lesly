"""Fase 2.3 (pieza 2): services/ofertas.py — precio vigente y descuento.

Contra la BD real dentro de transacciones con rollback: nada persiste.
Se usan productos y ofertas sintéticos (nunca datos del propietario).
Los tests puros (sin BD) verifican redondeo y clamp; los de BD
verifican vigencia (vigente/vencida/inactiva), rangos disjuntos, la
coincidencia SQL/Python y el constraint de db/08.
"""
import uuid as _uuid
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest
from psycopg2 import errors as _pg_errores

import db_supabase
from services.ofertas import (SQL_IMPORTE_LINEA, calcular_descuento_unitario,
                              oferta_vigente, precios_vigentes)


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


@pytest.fixture()
def fx():
    """Producto sintético (precio 9.99) dentro de una TXN con rollback."""
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(
        "INSERT INTO producto (nombre, precio) VALUES (%s, 9.99)"
        " RETURNING id_producto, precio",
        (f"test23-{_uuid.uuid4().hex[:10]}",))
    fila = cur.fetchone()
    yield {'cur': cur, 'proxy': proxy,
           'id_producto': fila['id_producto'], 'precio': fila['precio']}
    proxy.rollback()
    raw.close()


def _oferta(cur, id_producto, tipo, valor, inicio, fin, activa=True):
    """Inserta una oferta sintética; inicio y fin son expresiones SQL de timestamptz."""
    cur.execute(f"""
        INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio, fecha_fin, activa)
        VALUES (%s, %s, %s, {inicio}, {fin}, %s)
        RETURNING id_oferta
    """, (id_producto, tipo, valor, activa))
    return cur.fetchone()['id_oferta']


def _venta(cur):
    """Venta sintética online pendiente (id_cliente NULL: sin datos ajenos)."""
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
                %s)
        RETURNING id_venta
    """, (f"test23-{_uuid.uuid4().hex[:12]}",))
    return cur.fetchone()['id_venta']


# ── Redondeo (puros, sin BD) ──────────────────────────────────────

def test_redondeo_15_sobre_9_99():
    # 9.99 * 15 / 100 = 1.4985 -> 1.50 por unidad; final 8.49
    dcto = calcular_descuento_unitario(Decimal('9.99'), 'porcentaje', Decimal('15'))
    assert dcto == Decimal('1.50')
    assert Decimal('9.99') - dcto == Decimal('8.49')


def test_redondeo_half_up_125_sobre_1_00():
    # 1.00 * 12.5 / 100 = 0.125: HALF_UP (y el ROUND de PostgreSQL) dan 0.13;
    # HALF_EVEN (default de Decimal) daría 0.12
    assert calcular_descuento_unitario(Decimal('1.00'), 'porcentaje',
                                       Decimal('12.5')) == Decimal('0.13')


def test_redondeo_10_sobre_9_99():
    # 0.999 -> 1.00 (sube, no trunca)
    assert calcular_descuento_unitario(Decimal('9.99'), 'porcentaje',
                                       Decimal('10')) == Decimal('1.00')


# ── Clamp: el descuento nunca supera al precio (puros) ────────────

def test_descuento_nunca_mayor_al_precio():
    precio = Decimal('9.99')
    assert calcular_descuento_unitario(precio, 'porcentaje', 100) == precio
    assert calcular_descuento_unitario(precio, 'porcentaje', 150) == Decimal('0.00')
    # precio_fijo legado tras una baja de precio (valor > precio): clamp a 0
    assert calcular_descuento_unitario(precio, 'precio_fijo', 150) == Decimal('0.00')
    assert calcular_descuento_unitario(precio, 'precio_fijo',
                                       Decimal('7.50')) == Decimal('2.49')
    assert calcular_descuento_unitario(precio, 'otro_tipo', 10) == Decimal('0.00')
    for tipo, valor in (('porcentaje', 33), ('porcentaje', 99.9),
                        ('precio_fijo', 0.01), ('precio_fijo', 9.98)):
        assert 0 <= calcular_descuento_unitario(precio, tipo, valor) <= precio


# ── Vigencia contra la BD (rollback) ──────────────────────────────

def test_oferta_vigente_aplica_y_coincide_con_python(fx):
    cur = fx['cur']
    id_of = _oferta(cur, fx['id_producto'], 'porcentaje', 15,
                    "NOW() - INTERVAL '1 hour'", "NOW() + INTERVAL '1 day'")
    fila = oferta_vigente(cur, fx['id_producto'])
    assert fila is not None
    assert fila['id_oferta'] == id_of
    assert fila['tipo'] == 'porcentaje'
    assert fila['descuento_unitario'] == Decimal('1.50')
    assert fila['precio_final'] == Decimal('8.49')
    # SQL y Python son el mismo cálculo
    assert fila['descuento_unitario'] == calcular_descuento_unitario(
        fx['precio'], 'porcentaje', 15)


def test_oferta_vencida_no_aplica(fx):
    cur = fx['cur']
    _oferta(cur, fx['id_producto'], 'porcentaje', 15,
            "NOW() - INTERVAL '3 days'", "NOW() - INTERVAL '1 day'")
    fila = oferta_vigente(cur, fx['id_producto'])
    assert fila is not None
    assert fila['id_oferta'] is None
    assert fila['descuento_unitario'] == Decimal('0')
    assert fila['precio_final'] == fx['precio']


def test_oferta_inactiva_no_aplica(fx):
    cur = fx['cur']
    _oferta(cur, fx['id_producto'], 'porcentaje', 15,
            "NOW() - INTERVAL '1 hour'", "NOW() + INTERVAL '1 day'", activa=False)
    fila = oferta_vigente(cur, fx['id_producto'])
    assert fila is not None
    assert fila['id_oferta'] is None
    assert fila['descuento_unitario'] == Decimal('0')


def test_rangos_disjuntos_aplican_la_vigente(fx):
    cur = fx['cur']
    # Tres activas en rangos disjuntos (exclusion lo permite): solo
    # la que cubre NOW() aplica en este instante.
    _oferta(cur, fx['id_producto'], 'porcentaje', 5,
            "NOW() - INTERVAL '3 days'", "NOW() - INTERVAL '1 day'")     # pasada
    id_vigente = _oferta(cur, fx['id_producto'], 'porcentaje', 15,
                         "NOW() - INTERVAL '1 hour'", "NOW() + INTERVAL '1 hour'")
    _oferta(cur, fx['id_producto'], 'porcentaje', 25,
            "NOW() + INTERVAL '2 days'", "NOW() + INTERVAL '3 days'")    # futura
    fila = oferta_vigente(cur, fx['id_producto'])
    assert fila['id_oferta'] == id_vigente
    assert fila['descuento_unitario'] == Decimal('1.50')


def test_lote_precios_vigentes(fx):
    cur = fx['cur']
    cur.execute(
        "INSERT INTO producto (nombre, precio) VALUES (%s, 1.00) RETURNING id_producto",
        (f"test23-{_uuid.uuid4().hex[:10]}",))
    id_p2 = cur.fetchone()['id_producto']
    _oferta(cur, fx['id_producto'], 'porcentaje', 15,
            "NOW() - INTERVAL '1 hour'", "NOW() + INTERVAL '1 day'")
    lote = precios_vigentes(cur, [fx['id_producto'], id_p2])
    assert set(lote) == {fx['id_producto'], id_p2}
    assert lote[fx['id_producto']]['descuento_unitario'] == Decimal('1.50')
    assert lote[fx['id_producto']]['precio_final'] == Decimal('8.49')
    assert lote[id_p2]['id_oferta'] is None
    assert lote[id_p2]['descuento_unitario'] == Decimal('0')
    assert lote[id_p2]['precio_final'] == Decimal('1.00')


def test_sql_rama_125_sobre_1_00(fx):
    """La rama SQL del LATERAL redondea igual que Python: 0.125 -> 0.13.

    PostgreSQL ROUND (half away from zero) y ROUND_HALF_UP coinciden;
    un ROUND_HALF_EVEN daría 0.12 y descuadraría SQL contra Python.
    """
    cur = fx['cur']
    cur.execute(
        "INSERT INTO producto (nombre, precio) VALUES (%s, 1.00) RETURNING id_producto",
        (f"test23-{_uuid.uuid4().hex[:10]}",))
    id_p2 = cur.fetchone()['id_producto']
    id_of = _oferta(cur, id_p2, 'porcentaje', 12.5,
                    "NOW() - INTERVAL '1 hour'", "NOW() + INTERVAL '1 day'")
    fila = oferta_vigente(cur, id_p2)
    assert fila['id_oferta'] == id_of
    assert fila['descuento_unitario'] == Decimal('0.13')
    assert fila['precio_final'] == Decimal('0.87')
    assert fila['descuento_unitario'] == calcular_descuento_unitario(
        Decimal('1.00'), 'porcentaje', Decimal('12.5'))


# ── Fórmula única y constraint db/08 (rollback) ────────────────────

def test_sql_importe_linea_con_descuento_congelado(fx):
    """3 x (9.99 - 1.50) = 25.47 con SQL_IMPORTE_LINEA (15% sobre 9.99)."""
    cur = fx['cur']
    id_of = _oferta(cur, fx['id_producto'], 'porcentaje', 15,
                    "NOW() - INTERVAL '1 hour'", "NOW() + INTERVAL '1 day'")
    id_venta = _venta(cur)
    cur.execute("""
        INSERT INTO detalle_venta
            (id_venta, id_producto, cantidad, precio_unitario, id_oferta, descuento_unitario)
        VALUES (%s, %s, 3, 9.99, %s, 1.50)
    """, (id_venta, fx['id_producto'], id_of))
    cur.execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
          FROM detalle_venta WHERE id_venta = %s
    """, (id_venta,))
    assert cur.fetchone()['total'] == Decimal('25.47')


def test_db08_bloquea_descuento_mayor_al_precio(fx):
    """db/08: la BD rechaza persistir descuento_unitario > precio_unitario."""
    cur = fx['cur']
    id_venta = _venta(cur)
    cur.execute(
        "INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)"
        " VALUES (%s, %s, 1, 9.99)", (id_venta, fx['id_producto']))
    with pytest.raises(_pg_errores.CheckViolation) as ei:
        cur.execute(
            "UPDATE detalle_venta SET descuento_unitario = precio_unitario + 0.01"
            " WHERE id_venta = %s", (id_venta,))
    assert ei.value.diag.constraint_name == 'chk_detalle_descuento_coherente'
