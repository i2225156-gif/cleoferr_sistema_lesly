"""Reporte por rango de fechas de /reportes (de verdad, no deshabilitado).

El formulario de /reportes estaba con el fieldset disabled y sin ruta
(/reportes/generar no existia). Ahora hay una ruta GET con el mismo
permiso que arma la tabla paginada (20 por pagina), el resumen sobre
TODO el conjunto filtrado y el estado vacio legible.

Tipos: ventas (cantidad y total neto, solo pagos confirmados), pedidos
(cantidad por estado), productos (mas vendidos) e inventario (movimientos
por tipo). Rango en dias calendario Lima, politique unica en
services/horario.validar_rango_fechas.

Datos sinteticos dentro de una transaccion con rollback. Las
expectativas son RELATIVAS: se comparan con la misma consulta SQL sobre
el rango (la BD real tambien tiene ventas).
"""
import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app

LIMA = timezone(timedelta(hours=-5))
POR_PAGINA = 20
NETO = "(d.cantidad * (d.precio_unitario - COALESCE(d.descuento_unitario, 0)))"


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


def _sesion(rol='administrador', uid=7):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)

    cur.execute("SELECT (NOW() AT TIME ZONE 'America/Lima')::date AS hoy")
    hoy = cur.fetchone()['hoy']

    def ts(dias, hh=12, mm=0, ss=0):
        d = hoy + timedelta(days=dias)
        return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=LIMA)

    marca = _uuid.uuid4().hex[:8]
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, stock_minimo, destacado, activo)
        VALUES (%s, 10.00, 100, 5, FALSE, TRUE) RETURNING id_producto
    """, (f'zzrep-prod-{marca}',))
    id_producto = cur.fetchone()['id_producto']

    def venta(fecha, estado, pago, nombre, cantidad=2):
        cur.execute("""
            INSERT INTO venta (fecha, id_tipo_venta, id_estado_venta, cliente_nombre)
            VALUES (%s, 1, %s, %s) RETURNING id_venta
        """, (fecha, estado, nombre))
        id_venta = cur.fetchone()['id_venta']
        cur.execute("""
            INSERT INTO detalle_venta (id_venta, id_producto, cantidad,
                                       precio_unitario, nombre_producto,
                                       descuento_unitario)
            VALUES (%s, %s, %s, 10.00, %s, 0)
        """, (id_venta, id_producto, cantidad, f'zzrep-prod-{marca}'))
        if pago:
            cur.execute("""
                INSERT INTO pago (id_venta, id_tipo_pago, estado, fecha_pago)
                VALUES (%s, 1, %s, %s)
            """, (id_venta, pago, fecha))
        return id_venta

    # Venta al borde: 23:59:59 de Lima del dia anterior, pago confirmado.
    id_venta_borde = venta(ts(-1, 23, 59, 59), 6, 'confirmado',
                           f'zzrepbase-borde-{marca}')
    # Fuera del rango por defecto (hace 40 dias).
    id_venta_vieja = venta(ts(-40, 12), 6, 'confirmado',
                           f'zzrepbase-vieja-{marca}', cantidad=1)
    # Pendiente sin pago: cuenta en pedidos, NO en ventas.
    id_venta_pendiente = venta(ts(-2, 8), 1, None,
                                f'zzrepbase-pend-{marca}')
    # Pago no confirmado: cuenta en pedidos, NO en ventas.
    id_venta_preparando = venta(ts(-3, 9), 2, 'pendiente',
                                 f'zzrepbase-prep-{marca}')

    # Movimientos de inventario (entradas y salidas).
    cur.execute("""
        INSERT INTO inventario_movimiento (tipo, id_producto, cantidad,
                                           precio_unitario, stock_resultante,
                                           observacion, fecha)
        VALUES ('entrada', %s, 5, 10.00, 105, 'zzrep-entrada', %s),
               ('salida', %s, 2, 10.00, 103, 'zzrep-salida', %s)
    """, (id_producto, ts(-3, 10), id_producto, ts(-1, 18)))

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'admin': _sesion(), 'hoy': hoy,
           'id_producto': id_producto, 'marca': marca,
           'id_venta_borde': id_venta_borde,
           'id_venta_vieja': id_venta_vieja,
           'id_venta_pendiente': id_venta_pendiente,
           'id_venta_preparando': id_venta_preparando}
    proxy.rollback()
    raw.close()


def _iso(fecha):
    return fecha.strftime('%Y-%m-%d')


def _dias(fx, dias):
    return _iso(fx['hoy'] + timedelta(days=dias))


def _reporte(fx, tipo='ventas', desde=None, hasta=None, pagina=None):
    args = [f'tipo={tipo}']
    if desde:
        args.append(f'desde={desde}')
    if hasta:
        args.append(f'hasta={hasta}')
    if pagina:
        args.append(f'pagina={pagina}')
    return fx['admin'].get('/reportes/generar?' + '&'.join(args))


def _html(r):
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _filas(html):
    return html.count('class="fila-reporte"')


def _ids_ventas(html):
    return [int(x) for x in
            re.findall(r'fila-reporte">\s*<td>(\d+)</td>', html)]


def _resumen(html, clave):
    m = re.search(r'data-resumen="%s"[^>]*>([^<]*)<' % clave, html)
    assert m, f'no esta el resumen «{clave}» en la pagina'
    return m.group(1).strip()


def _vacio(html):
    return 'id="reporte-vacio"' in html


# ── Expectativas RELATIVAS (la BD real tambien tiene datos) ──────────

def _sql_ventas(fx, desde, hasta):
    """(cantidad, total neto) de las ventas con pago confirmado del rango."""
    fx['cur'].execute(f"""
        SELECT COUNT(*) AS c, COALESCE(SUM(total), 0) AS t FROM (
            SELECT v.id_venta, COALESCE(SUM({NETO}), 0) AS total
              FROM venta v
              JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
              LEFT JOIN detalle_venta d ON d.id_venta = v.id_venta
             WHERE (v.fecha AT TIME ZONE 'America/Lima')::date BETWEEN %s AND %s
             GROUP BY v.id_venta) x
    """, (desde, hasta))
    fila = fx['cur'].fetchone()
    return int(fila['c']), float(fila['t'] or 0)


def _sql_pedidos(fx, desde, hasta):
    fx['cur'].execute("""
        SELECT COUNT(*) AS c, COUNT(DISTINCT v.id_estado_venta) AS e
          FROM venta v
         WHERE (v.fecha AT TIME ZONE 'America/Lima')::date BETWEEN %s AND %s
    """, (desde, hasta))
    fila = fx['cur'].fetchone()
    return int(fila['c']), int(fila['e'])


# ── Ventas: cantidad y total neto (solo pagos confirmados) ───────────

def test_ventas_lista_solo_las_ventas_con_pago_confirmado_del_rango(fx):
    """La venta con pago confirmado entra; la pendiente (sin pago) y la
    de pago no confirmado no."""
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'ventas', desde=desde, hasta=hasta))
    ids = _ids_ventas(html)
    assert fx['id_venta_borde'] in ids
    assert fx['id_venta_pendiente'] not in ids
    assert fx['id_venta_preparando'] not in ids


def test_ventas_el_resumen_usa_el_total_neto_y_la_cantidad(fx):
    """El total es el neto (cantidad * (precio - descuento)) y la cantidad
    cuenta todas las ventas del rango, no solo las de la pagina."""
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'ventas', desde=desde, hasta=hasta))
    esperado_cantidad, esperado_total = _sql_ventas(fx, desde, hasta)
    assert int(_resumen(html, 'cantidad')) == esperado_cantidad
    assert float(_resumen(html, 'total')) == pytest.approx(esperado_total, abs=0.01)


def test_ventas_el_total_no_depende_de_la_pagina(fx):
    """Con mas de 20 ventas la tabla muestra 20 filas, pero el resumen
    cuenta el conjunto filtrado completo."""
    cur = fx['cur']
    marca = fx['marca']
    hoy = fx['hoy']
    cur.execute("""
        INSERT INTO venta (fecha, id_tipo_venta, id_estado_venta, cliente_nombre)
        SELECT %s, 1, 6, %s || g FROM generate_series(1, 25) g
        RETURNING id_venta
    """, (datetime(hoy.year, hoy.month, hoy.day, 9, tzinfo=LIMA),
          f'zzrepmany-cli-{marca}-'))
    cur.execute("""
        INSERT INTO detalle_venta (id_venta, id_producto, cantidad,
                                   precio_unitario, nombre_producto,
                                   descuento_unitario)
        SELECT v.id_venta, %s, 1, 10.00, 'zzrep-prod', 0
          FROM venta v WHERE v.cliente_nombre LIKE %s
    """, (fx['id_producto'], f'zzrepmany-cli-{marca}-%'))
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, estado, fecha_pago)
        SELECT v.id_venta, 1, 'confirmado', v.fecha
          FROM venta v WHERE v.cliente_nombre LIKE %s
    """, (f'zzrepmany-cli-{marca}-%',))
    desde, hasta = _dias(fx, -30), _dias(fx, 0)
    esperado, _ = _sql_ventas(fx, desde, hasta)
    assert esperado > POR_PAGINA
    html = _html(_reporte(fx, 'ventas', desde=desde, hasta=hasta))
    assert _filas(html) == POR_PAGINA
    assert int(_resumen(html, 'cantidad')) == esperado


def test_ventas_la_paginacion_conserva_los_filtros(fx):
    """Los enlaces de la macro llevan tipo/desde/hasta (no solo pagina)."""
    desde, hasta = _dias(fx, -30), _dias(fx, 0)
    html = _html(_reporte(fx, 'ventas', desde=desde, hasta=hasta))
    assert 'pagina=' in html
    assert 'tipo=ventas' in html
    assert f'desde={desde}' in html
    assert f'hasta={hasta}' in html


# ── Pedidos: cantidad por estado ──────────────────────────────────────

def test_pedidos_cuenta_por_estado(fx):
    """La tabla de pedidos trae una fila por estado con su cantidad."""
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'pedidos', desde=desde, hasta=hasta))
    _, estados = _sql_pedidos(fx, desde, hasta)
    assert _filas(html) == estados
    for estado in ('confirmado', 'pendiente', 'preparando'):
        assert estado in html


def test_pedidos_el_resumen_cuenta_todo_el_conjunto(fx):
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'pedidos', desde=desde, hasta=hasta))
    pedidos, _ = _sql_pedidos(fx, desde, hasta)
    assert int(_resumen(html, 'cantidad')) == pedidos


# ── Política de fechas ────────────────────────────────────────────────

def test_el_borde_de_2339_lima_entra_en_su_dia_y_no_en_el_siguiente(fx):
    """La venta de las 23:59:59 de Lima del dia anterior cuenta en ese
    dia (y no en el anterior)."""
    html = _html(_reporte(fx, 'ventas', desde=_dias(fx, -1), hasta=_dias(fx, -1)))
    assert fx['id_venta_borde'] in _ids_ventas(html)
    html = _html(_reporte(fx, 'ventas', desde=_dias(fx, -2), hasta=_dias(fx, -2)))
    assert fx['id_venta_borde'] not in _ids_ventas(html)


def test_el_rango_por_defecto_son_los_ultimos_30_dias(fx):
    """Sin fechas: los ultimos 30 dias (la venta de hace 40 dias queda
    fuera); el rango explicito la trae."""
    html = _html(_reporte(fx, 'ventas'))
    total_defecto = int(_resumen(html, 'cantidad'))
    esperado_defecto, _ = _sql_ventas(fx, _dias(fx, -29), _dias(fx, 0))
    assert total_defecto == esperado_defecto
    html = _html(_reporte(fx, 'ventas', desde=_dias(fx, -60), hasta=_dias(fx, 0)))
    total_amplio = int(_resumen(html, 'cantidad'))
    esperado_amplio, _ = _sql_ventas(fx, _dias(fx, -59), _dias(fx, 0))
    assert total_amplio == esperado_amplio
    assert esperado_amplio >= esperado_defecto + 1   # la venta de hace 40 dias


def test_fecha_invalida_no_rompe_la_pagina(fx):
    """Formato invalido = caso seguro (rango por defecto) con aviso."""
    html = _html(_reporte(fx, 'ventas', desde='no-es-fecha'))
    esperado, _ = _sql_ventas(fx, _dias(fx, -29), _dias(fx, 0))
    assert int(_resumen(html, 'cantidad')) == esperado
    assert 'Fecha inválida' in html


def test_hasta_futuro_se_recorta_a_hoy(fx):
    """«hasta» no puede estar en el futuro: se recorta a hoy."""
    desde = _dias(fx, -30)
    html = _html(_reporte(fx, 'ventas', desde=desde, hasta=_dias(fx, 10)))
    esperado, _ = _sql_ventas(fx, desde, _dias(fx, 0))
    assert int(_resumen(html, 'cantidad')) == esperado
    assert 'futuro' in html.lower()


def test_desde_posterior_a_hasta_se_avisa_y_no_rompe(fx):
    """desde > hasta: aviso y un rango coherente (desde <= hasta)."""
    html = _html(_reporte(fx, 'ventas', desde=_dias(fx, 0), hasta=_dias(fx, -10)))
    assert 'posterior' in html.lower()
    esperado, _ = _sql_ventas(fx, _dias(fx, -10), _dias(fx, 0))
    assert int(_resumen(html, 'cantidad')) == esperado


def test_tipo_desconocido_cae_en_la_lista_blanca(fx):
    """Un tipo fuera de la lista blanca no da 500: se avisa y se usa
    ventas."""
    html = _html(_reporte(fx, 'zzz-inventario'))
    esperado, _ = _sql_ventas(fx, _dias(fx, -29), _dias(fx, 0))
    assert int(_resumen(html, 'cantidad')) == esperado
    assert 'zzz-inventario' in html


# ── Estado vacío y permiso ────────────────────────────────────────────

def test_estado_vacio_legible_sin_500(fx):
    """Un rango sin datos muestra el estado vacio, no un 500."""
    html = _html(_reporte(fx, 'ventas', desde=_dias(fx, -300), hasta=_dias(fx, -200)))
    assert _vacio(html)
    assert _filas(html) == 0


def test_sin_permiso_de_reportes_no_entra(fx, monkeypatch):
    """Un vendedor sin el permiso «reportes» recibe 302 a su inicio."""
    class CursorFalso:
        def execute(self, sql, params=None):
            self._filas = [{'activo': True, 'permiso': None,
                            'plantilla_sembrada': True}]

        def fetchall(self):
            return self._filas

        def fetchone(self):
            return self._filas[0]

    class ConnFalso:
        def cursor(self, dictionary=False, **kwargs):
            return CursorFalso()

        def close(self):
            pass

    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: ConnFalso())
    c = _sesion('vendedor', 8)
    r = c.get('/reportes/generar?tipo=ventas')
    assert r.status_code == 302
    assert '/inicio_personal' in r.headers['Location']


# ── El formulario de /reportes ya no esta deshabilitado ───────────────

def test_el_formulario_de_reportes_ya_no_esta_deshabilitado(fx):
    """Sin nota de «temporalmente deshabilitado», sin fieldset disabled y
    con la ruta real del reporte."""
    html = _html(fx['admin'].get('/reportes'))
    assert 'temporalmente' not in html
    assert '<fieldset disabled>' not in html
    assert 'action="/reportes/generar"' in html
