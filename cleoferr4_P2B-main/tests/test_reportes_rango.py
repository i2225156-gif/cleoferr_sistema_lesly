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
import html as _html_lib
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


class CursorSinPermisos:
    """Doble de la conexión AUTH: el usuario existe pero no tiene filas de
    permiso (plantilla ya sembrada)."""

    def execute(self, sql, params=None):
        self._filas = [{'activo': True, 'permiso': None,
                        'plantilla_sembrada': True}]

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0]


class ConnSinPermisos:
    def cursor(self, dictionary=False, **kwargs):
        return CursorSinPermisos()

    def close(self):
        pass


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

    # Movimientos de inventario (entradas y salidas) y uno al borde:
    # 23:59:59 de Lima del dia anterior.
    cur.execute("""
        INSERT INTO inventario_movimiento (tipo, id_producto, cantidad,
                                           precio_unitario, stock_resultante,
                                           observacion, fecha)
        VALUES ('entrada', %s, 5, 10.00, 105, 'zzrep-entrada', %s),
               ('salida', %s, 2, 10.00, 103, 'zzrep-salida', %s),
               ('salida', %s, 1, 10.00, 102, 'zzrep-borde', %s)
        RETURNING id_movimiento
    """, (id_producto, ts(-3, 10), id_producto, ts(-1, 18),
          id_producto, ts(-1, 23, 59, 59)))
    ids_mov = [f['id_movimiento'] for f in cur.fetchall()]
    id_mov_entrada, id_mov_salida, id_mov_borde = ids_mov

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'proxy': proxy, 'admin': _sesion(), 'hoy': hoy,
           'id_producto': id_producto, 'marca': marca,
           'id_venta_borde': id_venta_borde,
           'id_venta_vieja': id_venta_vieja,
           'id_venta_pendiente': id_venta_pendiente,
           'id_venta_preparando': id_venta_preparando,
           'id_mov_entrada': id_mov_entrada,
           'id_mov_salida': id_mov_salida,
           'id_mov_borde': id_mov_borde}
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


def _ids(html):
    """La primera celda de cada fila (id de venta o de movimiento)."""
    return [int(x) for x in
            re.findall(r'fila-reporte">\s*<td>(\d+)</td>', html)]


def _fila_producto(html, nombre):
    """(unidades, monto) de la fila del producto en la tabla."""
    m = re.search(
        r'fila-reporte">\s*<td>%s</td>\s*<td>(\d+)</td>\s*<td>([\d.]+)</td>'
        % re.escape(nombre), html)
    assert m, f'no esta el producto «{nombre}» en la tabla'
    return int(m.group(1)), float(m.group(2))


def _resumen(html, clave):
    m = re.search(r'data-resumen="%s"[^>]*>([^<]*)<' % clave, html)
    assert m, f'no esta el resumen «{clave}» en la pagina'
    return m.group(1).strip()


def _vacio(html):
    return 'id="reporte-vacio"' in html


def _excel(resp):
    """El Excel de la respuesta como filas de texto (encabezado + datos)."""
    import io
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.get_data()))
    ws = wb.active
    return [[('' if c.value is None else str(c.value)) for c in fila]
            for fila in ws.iter_rows()]


def _celdas(html):
    """Las filas de la tabla del reporte como listas de texto."""
    cuerpo = html[html.find('<tbody>'):html.find('</tbody>')]
    filas = []
    for m in re.finditer(r'<tr class="fila-reporte">(.*?)</tr>', cuerpo, re.S):
        filas.append([_html_lib.unescape(re.sub(r'<[^>]+>', '', c)).strip()
                      for c in re.findall(r'<td>(.*?)</td>', m.group(1), re.S)])
    return filas


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


def _sql_productos(fx, desde, hasta):
    """(productos, unidades, monto neto) del rango: la misma consulta."""
    fx['cur'].execute(f"""
        SELECT COUNT(*) AS c, COALESCE(SUM(unidades), 0) AS u,
               COALESCE(SUM(monto), 0) AS m FROM (
            SELECT p.id_producto, SUM(d.cantidad) AS unidades,
                   SUM({NETO}) AS monto
              FROM detalle_venta d
              JOIN venta v ON v.id_venta = d.id_venta
              JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
              JOIN producto p ON p.id_producto = d.id_producto
             WHERE (v.fecha AT TIME ZONE 'America/Lima')::date BETWEEN %s AND %s
             GROUP BY p.id_producto) x
    """, (desde, hasta))
    fila = fx['cur'].fetchone()
    return int(fila['c']), int(fila['u']), float(fila['m'] or 0)


def _sql_inventario(fx, desde, hasta):
    """(movimientos, unidades de entrada, unidades de salida) del rango."""
    fx['cur'].execute("""
        SELECT COUNT(*) AS c,
               COALESCE(SUM(CASE WHEN tipo = 'entrada' THEN cantidad ELSE 0 END), 0) AS e,
               COALESCE(SUM(CASE WHEN tipo = 'salida' THEN cantidad ELSE 0 END), 0) AS s
          FROM inventario_movimiento
         WHERE (fecha AT TIME ZONE 'America/Lima')::date BETWEEN %s AND %s
    """, (desde, hasta))
    fila = fx['cur'].fetchone()
    return int(fila['c']), int(fila['e']), int(fila['s'])


# ── Ventas: cantidad y total neto (solo pagos confirmados) ───────────

def test_ventas_lista_solo_las_ventas_con_pago_confirmado_del_rango(fx):
    """La venta con pago confirmado entra; la pendiente (sin pago) y la
    de pago no confirmado no."""
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'ventas', desde=desde, hasta=hasta))
    ids = _ids(html)
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


# ── Productos: mas vendidos por unidades y monto ──────────────────────

def test_productos_lista_los_mas_vendidos_con_unidades_y_monto(fx):
    """El producto del fixture sale con sus unidades y su monto neto; las
    ventas sin pago confirmado (o fuera del rango) no cuentan."""
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'productos', desde=desde, hasta=hasta))
    unidades, monto = _fila_producto(html, f'zzrep-prod-{fx["marca"]}')
    assert unidades == 2
    assert monto == pytest.approx(20.00, abs=0.01)


def test_productos_el_resumen_cuenta_todo_el_conjunto(fx):
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'productos', desde=desde, hasta=hasta))
    productos, unidades, monto = _sql_productos(fx, desde, hasta)
    assert int(_resumen(html, 'cantidad')) == productos
    assert int(_resumen(html, 'unidades')) == unidades
    assert float(_resumen(html, 'monto')) == pytest.approx(monto, abs=0.01)


def test_productos_el_total_no_depende_de_la_pagina(fx):
    """Con mas de 20 productos la tabla muestra 20 filas, pero el resumen
    cuenta el conjunto filtrado completo."""
    cur = fx['cur']
    marca = fx['marca']
    hoy = fx['hoy']
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, stock_minimo, destacado, activo)
        SELECT %s || g, 10.00, 100, 5, FALSE, TRUE FROM generate_series(1, 25) g
        RETURNING id_producto
    """, (f'zzrepmany-prod-{marca}-',))
    ids_productos = [f['id_producto'] for f in cur.fetchall()]
    cur.execute("""
        INSERT INTO venta (fecha, id_tipo_venta, id_estado_venta, cliente_nombre)
        SELECT %s, 1, 6, %s || g FROM generate_series(1, 25) g
        RETURNING id_venta
    """, (datetime(hoy.year, hoy.month, hoy.day, 9, tzinfo=LIMA),
          f'zzrepmany-cli-{marca}-'))
    ids_ventas = [f['id_venta'] for f in cur.fetchall()]
    for id_venta, id_producto in zip(ids_ventas, ids_productos):
        cur.execute("""
            INSERT INTO detalle_venta (id_venta, id_producto, cantidad,
                                       precio_unitario, nombre_producto,
                                       descuento_unitario)
            VALUES (%s, %s, 1, 10.00, 'zzrepmany', 0)
        """, (id_venta, id_producto))
        cur.execute("""
            INSERT INTO pago (id_venta, id_tipo_pago, estado, fecha_pago)
            VALUES (%s, 1, 'confirmado', NOW())
        """, (id_venta,))
    desde, hasta = _dias(fx, -30), _dias(fx, 0)
    productos, unidades, monto = _sql_productos(fx, desde, hasta)
    assert productos > POR_PAGINA
    html = _html(_reporte(fx, 'productos', desde=desde, hasta=hasta))
    assert _filas(html) == POR_PAGINA
    assert int(_resumen(html, 'cantidad')) == productos
    assert int(_resumen(html, 'unidades')) == unidades
    assert float(_resumen(html, 'monto')) == pytest.approx(monto, abs=0.01)


# ── Inventario: movimientos por tipo (entradas y salidas) ──────────────

def test_inventario_lista_las_entradas_y_las_salidas_del_rango(fx):
    """Los movimientos del rango salen con su tipo, su producto y su
    cantidad."""
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'inventario', desde=desde, hasta=hasta))
    ids = _ids(html)
    assert fx['id_mov_entrada'] in ids
    assert fx['id_mov_salida'] in ids
    assert fx['id_mov_borde'] in ids
    assert 'entrada' in html and 'salida' in html


def test_inventario_el_resumen_cuenta_entradas_y_salidas_del_conjunto(fx):
    desde, hasta = _dias(fx, -3), _dias(fx, -1)
    html = _html(_reporte(fx, 'inventario', desde=desde, hasta=hasta))
    movimientos, entradas, salidas = _sql_inventario(fx, desde, hasta)
    assert int(_resumen(html, 'cantidad')) == movimientos
    assert int(_resumen(html, 'entradas')) == entradas
    assert int(_resumen(html, 'salidas')) == salidas


def test_inventario_el_borde_de_2339_lima_entra_en_su_dia(fx):
    """El movimiento de las 23:59:59 de Lima del dia anterior cuenta en
    ese dia (y no en el anterior) y sale a las 23:59 (hora Lima)."""
    html = _html(_reporte(fx, 'inventario',
                          desde=_dias(fx, -1), hasta=_dias(fx, -1)))
    assert fx['id_mov_borde'] in _ids(html)
    dia = fx['hoy'] - timedelta(days=1)
    assert f'{dia.strftime("%d/%m/%Y")} 23:59' in html
    html = _html(_reporte(fx, 'inventario',
                          desde=_dias(fx, -2), hasta=_dias(fx, -2)))
    assert fx['id_mov_borde'] not in _ids(html)


def test_inventario_el_total_no_depende_de_la_pagina(fx):
    """Con mas de 20 movimientos la tabla muestra 20 filas, pero el resumen
    cuenta el conjunto filtrado completo."""
    cur = fx['cur']
    hoy = fx['hoy']
    cur.execute("""
        INSERT INTO inventario_movimiento (tipo, id_producto, cantidad,
                                           precio_unitario, stock_resultante,
                                           observacion, fecha)
        SELECT 'entrada', %s, 3, 10.00, 100, 'zzrepmany-mov',
               %s + (g || ' minutes')::interval
          FROM generate_series(1, 25) g
    """, (fx['id_producto'],
          datetime(hoy.year, hoy.month, hoy.day, 6, tzinfo=LIMA)))
    desde, hasta = _dias(fx, -30), _dias(fx, 0)
    movimientos, entradas, salidas = _sql_inventario(fx, desde, hasta)
    assert movimientos > POR_PAGINA
    html = _html(_reporte(fx, 'inventario', desde=desde, hasta=hasta))
    assert _filas(html) == POR_PAGINA
    assert int(_resumen(html, 'cantidad')) == movimientos
    assert int(_resumen(html, 'entradas')) == entradas
    assert int(_resumen(html, 'salidas')) == salidas


# ── Política de fechas ────────────────────────────────────────────────

def test_el_borde_de_2339_lima_entra_en_su_dia_y_no_en_el_siguiente(fx):
    """La venta de las 23:59:59 de Lima del dia anterior cuenta en ese
    dia (y no en el anterior)."""
    html = _html(_reporte(fx, 'ventas', desde=_dias(fx, -1), hasta=_dias(fx, -1)))
    assert fx['id_venta_borde'] in _ids(html)
    html = _html(_reporte(fx, 'ventas', desde=_dias(fx, -2), hasta=_dias(fx, -2)))
    assert fx['id_venta_borde'] not in _ids(html)


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


def test_productos_e_inventario_tienen_estado_vacio(fx):
    """Los dos tipos nuevos tambien muestran el estado vacio (no un 500)
    cuando el rango no tiene datos."""
    for tipo in ('productos', 'inventario'):
        html = _html(_reporte(fx, tipo,
                              desde=_dias(fx, -300), hasta=_dias(fx, -200)))
        assert _vacio(html), tipo
        assert _filas(html) == 0, tipo


def test_sin_permiso_de_reportes_no_entra(fx, monkeypatch):
    """Un vendedor sin el permiso «reportes» recibe 302 a su inicio."""
    monkeypatch.setattr(modulo_app, 'get_connection_auth',
                        lambda: ConnSinPermisos())
    c = _sesion('vendedor', 8)
    r = c.get('/reportes/generar?tipo=ventas')
    assert r.status_code == 302
    assert '/inicio_personal' in r.headers['Location']


# ── Exportación a Excel (el mismo filtro, sin paginar) ────────────────

def test_el_excel_coincide_con_la_tabla_y_trae_todas_las_filas(fx):
    """El Excel lleva TODAS las filas del filtro (sin paginar) y coincide
    con la tabla: mismo encabezado, mismas filas y el total del resumen."""
    desde, hasta = _dias(fx, -30), _dias(fx, 0)
    html = _html(_reporte(fx, 'ventas', desde=desde, hasta=hasta))
    r = fx['admin'].get(f'/reportes/rango/exportar'
                        f'?tipo=ventas&desde={desde}&hasta={hasta}')
    assert r.status_code == 200
    assert 'spreadsheetml' in r.headers['Content-Type']
    filas = _excel(r)
    tabla = _celdas(html)
    assert filas[0] == ['Venta', 'Fecha (Perú)', 'Estado', 'Tipo', 'Total (S/)']
    # sin paginar: el Excel trae todas las filas del filtro
    assert len(filas) - 1 == int(_resumen(html, 'cantidad'))
    # y son las mismas que salen en la tabla (mismo filtro y mismo orden)
    assert filas[1:1 + len(tabla)] == tabla
    # el nombre del archivo lleva el tipo y el rango
    disposicion = r.headers['Content-Disposition']
    assert f'reporte_ventas_{desde}_{hasta}.xlsx' in disposicion


def test_los_cuatro_tipos_se_exportan_a_excel(fx):
    """Los cuatro tipos de la lista blanca se exportan, cada uno con su
    nombre de archivo (tipo y rango)."""
    desde, hasta = _dias(fx, -30), _dias(fx, 0)
    for tipo in ('ventas', 'productos', 'inventario', 'pedidos'):
        r = fx['admin'].get(f'/reportes/rango/exportar'
                            f'?tipo={tipo}&desde={desde}&hasta={hasta}')
        assert r.status_code == 200, tipo
        assert 'spreadsheetml' in r.headers['Content-Type'], tipo
        assert (f'reporte_{tipo}_{desde}_{hasta}.xlsx'
                in r.headers['Content-Disposition']), tipo


def test_el_excel_de_inventario_muestra_las_fechas_en_hora_lima(fx):
    """La fecha del Excel es la Pared Lima: las 23:59:59 se ven 23:59."""
    desde, hasta = _dias(fx, -1), _dias(fx, -1)
    r = fx['admin'].get('/reportes/rango/exportar'
                        f'?tipo=inventario&desde={desde}&hasta={hasta}')
    assert r.status_code == 200
    dia = fx['hoy'] - timedelta(days=1)
    esperado = f'{dia.strftime("%d/%m/%Y")} 23:59'
    assert any(esperado in celda for fila in _excel(r) for celda in fila)


def test_la_exportacion_exige_el_permiso_de_reportes(fx, monkeypatch):
    """Un vendedor sin el módulo «reportes» recibe 302 a su inicio."""
    monkeypatch.setattr(modulo_app, 'get_connection_auth',
                        lambda: ConnSinPermisos())
    r = _sesion('vendedor', 8).get('/reportes/rango/exportar?tipo=ventas')
    assert r.status_code == 302
    assert '/inicio_personal' in r.headers['Location']


def test_la_exportacion_de_un_rango_vacio_no_rompe(fx):
    """Un rango sin datos devuelve un Excel vacío (no un 500)."""
    r = fx['admin'].get('/reportes/rango/exportar?tipo=ventas'
                        f'&desde={_dias(fx, -300)}&hasta={_dias(fx, -200)}')
    assert r.status_code == 200
    assert 'spreadsheetml' in r.headers['Content-Type']
    assert _excel(r) == []


# ── El formulario de /reportes ya no esta deshabilitado ───────────────

def test_el_formulario_de_reportes_ya_no_esta_deshabilitado(fx):
    """Sin nota de «temporalmente deshabilitado», sin fieldset disabled y
    con la ruta real del reporte."""
    html = _html(fx['admin'].get('/reportes'))
    assert 'temporalmente' not in html
    assert '<fieldset disabled>' not in html
    assert 'action="/reportes/generar"' in html
