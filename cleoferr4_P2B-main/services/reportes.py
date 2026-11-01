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


def reporte(tipo, cursor, desde, hasta, pagina=1, por_pagina=20):
    """Reporte del tipo pedido. `pagina=None` = todas las filas (export)."""
    if tipo == 'ventas':
        return _reporte_ventas(cursor, desde, hasta, pagina, por_pagina)
    if tipo == 'pedidos':
        return _reporte_pedidos(cursor, desde, hasta, pagina, por_pagina)
    return {'headers': [], 'filas': [], 'total': 0, 'pagina': 1,
            'resumen': []}
