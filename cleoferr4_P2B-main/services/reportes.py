"""Consultas del reporte por rango de fechas (/reportes/generar).

Un solo lugar para las consultas de reportes:
- `sql_top_productos` es la consulta del RF12 (productos mas vendidos):
  la usan el panel de /reportes y el reporte «productos» del rango, sin
  duplicarla.
- El resto son las consultas del reporte por rango: ventas (solo pago
  confirmado, total NETO con la formula unica), pedidos (cantidad por
  estado), productos (mas vendidos) e inventario (movimientos por tipo).

El rango se filtra por dia calendario Lima (services.horario) y los
resumenes se calculan sobre TODO el conjunto filtrado, nunca sobre la
pagina.
"""
from services.horario import SQL_DIA_LIMA, SQL_PARED_LIMA
from services.ofertas import SQL_IMPORTE_LINEA

# Lista blanca de tipos de reporte (nada fuera de esto).
TIPOS_REPORTE = ('ventas', 'productos', 'inventario', 'pedidos')

NOMBRES_TIPO = {
    'ventas': 'Ventas',
    'productos': 'Productos',
    'inventario': 'Inventario',
    'pedidos': 'Pedidos',
}


def sql_rango(alias, col, desde, hasta):
    """Filtro de rango (desde/hasta inclusive) en dia calendario Lima."""
    dia = SQL_DIA_LIMA.format(p=alias, col=col)
    return f" AND {dia} >= %s AND {dia} <= %s", [desde, hasta]


def sql_top_productos(rango=''):
    """Consulta del RF12: productos mas vendidos por unidades y por monto.

    SOLO pagos confirmados (criterio §6.9), dias calendario Lima.
    `rango` es el fragmento SQL del filtro de fechas (con su AND).
    """
    return f"""
        SELECT p.id_producto, p.nombre,
               SUM(d.cantidad) AS unidades,
               SUM({SQL_IMPORTE_LINEA.format(p='d.')}) AS monto
          FROM detalle_venta d
          JOIN venta v ON v.id_venta = d.id_venta
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN producto p ON p.id_producto = d.id_producto
         WHERE 1=1{rango}
         GROUP BY p.id_producto, p.nombre
    """


def _pagina(total, pagina, por_pagina):
    maximo = max(1, (total + por_pagina - 1) // por_pagina)
    return max(1, min(int(pagina or 1), maximo))


def _paginar(total, pagina, por_pagina, sql, params):
    """Agrega LIMIT/OFFSET a la consulta (pagina=None = sin paginar)."""
    if pagina is None:
        return sql, list(params), 1
    pagina = _pagina(total, pagina, por_pagina)
    return (sql + " LIMIT %s OFFSET %s",
            list(params) + [por_pagina, (pagina - 1) * por_pagina],
            pagina)


def _reporte_ventas(cursor, desde, hasta, pagina, por_pagina):
    """Ventas del rango: SOLO pagos confirmados; total NETO."""
    rango, params = sql_rango('v.', 'fecha', desde, hasta)
    base = f"""
        SELECT v.id_venta,
               {SQL_PARED_LIMA.format(p='v.', col='fecha')} AS fecha,
               ev.nombre AS estado,
               tv.nombre AS tipo,
               COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM venta v
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          LEFT JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
          LEFT JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
          LEFT JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE 1=1{rango}
         GROUP BY v.id_venta, v.fecha, ev.nombre, tv.nombre
    """
    cursor.execute(
        f"SELECT COUNT(*) AS c, COALESCE(SUM(total), 0) AS t FROM ({base}) x",
        params)
    agg = cursor.fetchone()
    total = int(agg['c'] or 0)
    sql, sql_params, pagina = _paginar(
        total, pagina, por_pagina, base + " ORDER BY fecha DESC, id_venta DESC",
        params)
    cursor.execute(sql, sql_params)
    filas = cursor.fetchall()
    return {
        'headers': ['Venta', 'Fecha (Perú)', 'Estado', 'Tipo', 'Total (S/)'],
        'filas': [[f['id_venta'],
                   f['fecha'].strftime('%d/%m/%Y %H:%M') if f['fecha'] else '–',
                   f['estado'] or '–', f['tipo'] or '–',
                   f"{float(f['total'] or 0):.2f}"] for f in filas],
        'total': total,
        'pagina': pagina,
        'resumen': [
            {'clave': 'cantidad', 'label': 'Ventas', 'valor': str(total)},
            {'clave': 'total', 'label': 'Total neto (S/)',
             'valor': f"{float(agg['t'] or 0):.2f}"},
        ],
    }


def _reporte_pedidos(cursor, desde, hasta, pagina, por_pagina):
    """Pedidos del rango: cantidad por estado de venta."""
    rango, params = sql_rango('v.', 'fecha', desde, hasta)
    base = f"""
        SELECT ev.nombre AS estado, ev.orden, COUNT(*) AS cantidad
          FROM venta v
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
         WHERE 1=1{rango}
         GROUP BY ev.nombre, ev.orden
    """
    cursor.execute(
        f"SELECT COUNT(*) AS c, COALESCE(SUM(cantidad), 0) AS t FROM ({base}) x",
        params)
    agg = cursor.fetchone()
    filas_conteo = int(agg['c'] or 0)
    pedidos = int(agg['t'] or 0)
    sql, sql_params, pagina = _paginar(
        filas_conteo, pagina, por_pagina,
        base + " ORDER BY orden ASC, estado ASC", params)
    cursor.execute(sql, sql_params)
    filas = cursor.fetchall()
    return {
        'headers': ['Estado', 'Pedidos'],
        'filas': [[f['estado'] or '–', int(f['cantidad'] or 0)] for f in filas],
        'total': filas_conteo,
        'pagina': pagina,
        'resumen': [
            {'clave': 'cantidad', 'label': 'Pedidos', 'valor': str(pedidos)},
            {'clave': 'estados', 'label': 'Estados',
             'valor': str(filas_conteo)},
        ],
    }


def _reporte_productos(cursor, desde, hasta, pagina, por_pagina):
    """Productos mas vendidos del rango: unidades y monto neto.

    Reutiliza `sql_top_productos` (la consulta del RF12): SOLO pagos
    confirmados y dias calendario Lima. El resumen es sobre TODO el
    conjunto filtrado, no sobre la pagina.
    """
    rango, params = sql_rango('v.', 'fecha', desde, hasta)
    base = sql_top_productos(rango)
    cursor.execute(f"""
        SELECT COUNT(*) AS c, COALESCE(SUM(unidades), 0) AS u,
               COALESCE(SUM(monto), 0) AS m
          FROM ({base}) x
    """, params)
    agg = cursor.fetchone()
    total = int(agg['c'] or 0)
    unidades = int(agg['u'] or 0)
    monto = float(agg['m'] or 0)
    sql, sql_params, pagina = _paginar(
        total, pagina, por_pagina,
        base + " ORDER BY unidades DESC, monto DESC, p.id_producto ASC", params)
    cursor.execute(sql, sql_params)
    filas = cursor.fetchall()
    return {
        'headers': ['Producto', 'Unidades', 'Monto (S/)'],
        'filas': [[f['nombre'], int(f['unidades'] or 0),
                   f"{float(f['monto'] or 0):.2f}"] for f in filas],
        'total': total,
        'pagina': pagina,
        'resumen': [
            {'clave': 'cantidad', 'label': 'Productos', 'valor': str(total)},
            {'clave': 'unidades', 'label': 'Unidades', 'valor': str(unidades)},
            {'clave': 'monto', 'label': 'Monto neto (S/)',
             'valor': f"{monto:.2f}"},
        ],
    }


def _reporte_inventario(cursor, desde, hasta, pagina, por_pagina):
    """Movimientos de inventario del rango: entradas y salidas.

    El filtro es por dia calendario Lima (services.horario) y la fecha
    que se muestra es la Pared Lima (SQL_PARED_LIMA): un movimiento de
    las 23:59:59 cuenta en su dia y sale a las 23:59.
    """
    rango, params = sql_rango('m.', 'fecha', desde, hasta)
    base = f"""
        SELECT m.id_movimiento, m.tipo,
               {SQL_PARED_LIMA.format(p='m.', col='fecha')} AS fecha,
               COALESCE(p.nombre, '(sin producto)') AS producto,
               m.cantidad, m.stock_resultante
          FROM inventario_movimiento m
          LEFT JOIN producto p ON p.id_producto = m.id_producto
         WHERE 1=1{rango}
    """
    cursor.execute(f"""
        SELECT COUNT(*) AS c,
               COALESCE(SUM(CASE WHEN tipo = 'entrada' THEN cantidad ELSE 0 END), 0) AS e,
               COALESCE(SUM(CASE WHEN tipo = 'salida' THEN cantidad ELSE 0 END), 0) AS s
          FROM ({base}) x
    """, params)
    agg = cursor.fetchone()
    total = int(agg['c'] or 0)
    entradas = int(agg['e'] or 0)
    salidas = int(agg['s'] or 0)
    sql, sql_params, pagina = _paginar(
        total, pagina, por_pagina,
        base + " ORDER BY fecha DESC, m.id_movimiento DESC", params)
    cursor.execute(sql, sql_params)
    filas = cursor.fetchall()
    return {
        'headers': ['Movimiento', 'Tipo', 'Fecha (Perú)', 'Producto',
                    'Cantidad', 'Stock'],
        'filas': [[f['id_movimiento'], f['tipo'] or '–',
                   f['fecha'].strftime('%d/%m/%Y %H:%M') if f['fecha'] else '–',
                   f['producto'] or '–', int(f['cantidad'] or 0),
                   f['stock_resultante'] if f['stock_resultante'] is not None else '–']
                  for f in filas],
        'total': total,
        'pagina': pagina,
        'resumen': [
            {'clave': 'cantidad', 'label': 'Movimientos', 'valor': str(total)},
            {'clave': 'entradas', 'label': 'Entradas (unidades)',
             'valor': str(entradas)},
            {'clave': 'salidas', 'label': 'Salidas (unidades)',
             'valor': str(salidas)},
        ],
    }


def reporte(tipo, cursor, desde, hasta, pagina=1, por_pagina=20):
    """Reporte del tipo pedido. `pagina=None` = todas las filas (export)."""
    if tipo == 'ventas':
        return _reporte_ventas(cursor, desde, hasta, pagina, por_pagina)
    if tipo == 'pedidos':
        return _reporte_pedidos(cursor, desde, hasta, pagina, por_pagina)
    if tipo == 'productos':
        return _reporte_productos(cursor, desde, hasta, pagina, por_pagina)
    if tipo == 'inventario':
        return _reporte_inventario(cursor, desde, hasta, pagina, por_pagina)
    return {'headers': [], 'filas': [], 'total': 0, 'pagina': 1,
            'resumen': []}
