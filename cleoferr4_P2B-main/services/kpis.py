"""KPIs empresariales del dashboard (aprobados: 3, 4, 5 y 6).

Cada KPI devuelve un dict con:
    valor     — número (float/int) o None si no hay datos
    texto     — cadena lista para renderizar ('42 d', '8.5%', '—')
    semaforo  — 'verde' | 'ambar' | 'rojo' | 'sin_datos'
    formula    — texto del tooltip (fórmula del cálculo)
    meta      — texto del tooltip (meta del semáforo)
    detalle   — línea adicional visible en la tarjeta (opcional)

Los umbrales de TODOS los semáforos viven aquí, como constantes en un
solo lugar. Todas las consultas son parametrizadas; las ventanas de
tiempo son relativas a NOW() y las ventas cuentan SOLO con pago
confirmado (criterio §6.9), excepto las canceladas/vencidas del KPI 4
que son estados de la venta.
"""
import os

from services.horario import SQL_DIA_LIMA, SQL_HOY_LIMA
from services.ofertas import SQL_IMPORTE_LINEA
from services.reservas import _plazo_segundos, _ventas_vencidas_sql


def umbral_vencimiento_dias():
    """Umbral de «próximo a vencer» en días.

    Variable de entorno VENCIMIENTO_UMBRAL_DIAS (default 30). La usan
    las alertas de vencimiento y el KPI de stock en riesgo: un solo
    lugar de configuración.
    """
    try:
        return max(1, int(os.environ.get('VENCIMIENTO_UMBRAL_DIAS', '30')))
    except ValueError:
        return 30

# ── Umbrales de semáforo (UN solo lugar) ─────────────────────────
# KPI 3 — días de cobertura de stock (ventas pagadas últimos 30 días)
ROTACION_DIAS_QUIEBRE = 15        # por debajo: rojo (riesgo de quiebres)
ROTACION_DIAS_VERDE_MIN = 30      # rango verde: 30–60 días
ROTACION_DIAS_VERDE_MAX = 60
ROTACION_DIAS_AMBAR_MAX = 90     # por encima: rojo (capital dormido)

# KPI 4 — % de pedidos online cancelados o con reserva vencida (30 días)
CANCELADOS_PCT_VERDE = 10.0      # <= 10% verde
CANCELADOS_PCT_AMBAR = 20.0      # 10–20 ámbar; > 20 rojo

# KPI 5 — tiempo medio de entrega, pendiente → entregado (online, 30 días)
ENTREGA_DIAS_VERDE = 2.0
ENTREGA_DIAS_AMBAR = 4.0

# KPI 6 — % del importe cobrado en EFECTIVO (30 días): concentración de caja
EFECTIVO_PCT_VERDE = 60.0        # <= 60% verde
EFECTIVO_PCT_AMBAR = 80.0        # 60–80 ámbar; > 80 rojo

# KPI 1 — margen bruto (hoy y mes en curso, Lima)
MARGEN_PCT_VERDE = 25.0          # >= 25% verde
MARGEN_PCT_AMBAR = 15.0          # 15–25 ámbar; < 15 rojo
MARGEN_COBERTURA_MINIMA = 60.0   # % de líneas con costo bajo el cual el
                                # semáforo es 'sin_datos' (no se inventa)

# KPI 2 — valor de stock en riesgo por vencimiento
RIESGO_PCT_VERDE = 2.0           # <= 2% del valor de inventario
RIESGO_PCT_AMBAR = 5.0           # 2–5 ámbar; > 5 rojo


def _semaforo_rotacion(dias):
    if dias < ROTACION_DIAS_QUIEBRE:
        return 'rojo'
    if dias < ROTACION_DIAS_VERDE_MIN:
        return 'ambar'
    if dias <= ROTACION_DIAS_VERDE_MAX:
        return 'verde'
    if dias <= ROTACION_DIAS_AMBAR_MAX:
        return 'ambar'
    return 'rojo'


def _semaforo_margen(pct):
    if pct is None:
        return 'sin_datos'
    if pct >= MARGEN_PCT_VERDE:
        return 'verde'
    if pct >= MARGEN_PCT_AMBAR:
        return 'ambar'
    return 'rojo'


def _margen_de(cursor, rango_sql):
    """(margen%, cobertura%) de las ventas pagadas de un rango, sobre las
    líneas cuyo producto TIENE costo. Cobertura = % del importe con
    costo: por debajo de MARGEN_COBERTURA_MINIMA el margen no es
    representativo y el semáforo va a 'sin_datos' (nunca se inventa)."""
    cursor.execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS importe,
               COALESCE(SUM(d.cantidad * p.precio_costo), 0) AS costo,
               COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')})
                        FILTER (WHERE p.precio_costo IS NOT NULL), 0) AS importe_con
          FROM detalle_venta d
          JOIN venta v ON v.id_venta = d.id_venta
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN producto p ON p.id_producto = d.id_producto
         WHERE {rango_sql}
    """)
    fila = cursor.fetchone()
    importe = float(fila['importe'] or 0)
    importe_con = float(fila['importe_con'] or 0)
    costo = float(fila['costo'] or 0)
    if importe <= 0 or importe_con <= 0:
        return None, 0.0
    margen = (importe_con - costo) / importe_con * 100.0
    cobertura = importe_con / importe * 100.0
    return round(margen, 1), round(cobertura, 1)


def kpi_margen_bruto(cursor):
    """KPI 1 — Margen bruto de HOY y del MES en curso (días Lima).

    (importe con costo − costo estimado) / importe con costo × 100.
    El costo es el precio_costo ACTUAL del producto aplicado a las
    líneas vendidas: aproximación honesta mientras no se congele el
    costo por línea (exigiría esquema adicional). El semáforo es del
    margen de HOY; el detalle muestra el mes y la cobertura.
    """
    hoy_sql = f"{SQL_DIA_LIMA.format(p='v.', col='fecha')} = {SQL_HOY_LIMA}"
    mes_sql = (f"{SQL_DIA_LIMA.format(p='v.', col='fecha')} >="
               " date_trunc('month', (NOW() AT TIME ZONE 'America/Lima')::date)")
    margen_hoy, cobertura = _margen_de(cursor, hoy_sql)
    margen_mes, _ = _margen_de(cursor, mes_sql)
    if margen_hoy is None or cobertura < MARGEN_COBERTURA_MINIMA:
        return {'valor': margen_hoy, 'valor_mes': margen_mes, 'texto': '—',
                'semaforo': 'sin_datos',
                'formula': '(importe con costo − costo) / importe con costo × 100, pagos confirmados',
                'meta': f'verde ≥ {MARGEN_PCT_VERDE:.0f}%; ámbar hasta {MARGEN_PCT_AMBAR:.0f}%',
                'detalle': (f'cobertura: {cobertura:.0f}% de líneas con costo '
                            f'(mínimo {MARGEN_COBERTURA_MINIMA:.0f}% para medir)')}
    return {'valor': margen_hoy, 'valor_mes': margen_mes,
            'texto': f'{margen_hoy}%',
            'semaforo': _semaforo_margen(margen_hoy),
            'formula': '(importe con costo − costo) / importe con costo × 100, pagos confirmados',
            'meta': (f'verde ≥ {MARGEN_PCT_VERDE:.0f}%; ámbar hasta '
                     f'{MARGEN_PCT_AMBAR:.0f}%; rojo por debajo'),
            'detalle': (f"mes {margen_mes}% · cobertura: {cobertura:.0f}% "
                        'de líneas con costo')}


def _semaforo_riesgo(pct):
    if pct is None:
        return 'sin_datos'
    if pct <= RIESGO_PCT_VERDE:
        return 'verde'
    if pct <= RIESGO_PCT_AMBAR:
        return 'ambar'
    return 'rojo'


def kpi_stock_riesgo(cursor):
    """KPI 2 — Valor de stock en riesgo por vencimiento.

    Valor (a costo si existe, a precio si no: COALESCE explícito) del
    stock activo vencido o que vence dentro del umbral
    (VENCIMIENTO_UMBRAL_DIAS), como % del valor total del inventario
    activo. La decisión que habilita: liquidar o devolver antes de
    perder — el KPI estrella para la dueña.
    """
    umbral = umbral_vencimiento_dias()
    cursor.execute("""
        SELECT COALESCE(SUM(stock * COALESCE(precio_costo, precio)) FILTER (
                        WHERE fecha_vencimiento IS NOT NULL
                          AND fecha_vencimiento <= CURRENT_DATE
                                  + make_interval(days => %s)), 0) AS riesgo,
               COALESCE(SUM(stock * COALESCE(precio_costo, precio)), 0) AS total
          FROM producto
         WHERE activo = TRUE
    """, (umbral,))
    fila = cursor.fetchone()
    riesgo = float(fila['riesgo'] or 0)
    total = float(fila['total'] or 0)
    if total <= 0:
        return {'valor': None, 'pct': None, 'texto': '—',
                'semaforo': 'sin_datos',
                'formula': 'stock vencido o por vencer × costo (o precio) ÷ valor total del inventario',
                'meta': f'verde ≤ {RIESGO_PCT_VERDE:.0f}%',
                'detalle': 'sin inventario activo'}
    pct = round(riesgo / total * 100.0, 1)
    return {'valor': round(riesgo, 2), 'pct': pct,
            'texto': f'S/ {riesgo:.2f} ({pct}%)',
            'semaforo': _semaforo_riesgo(pct),
            'formula': 'stock vencido o por vencer × costo (o precio) ÷ valor total del inventario',
            'meta': (f'verde ≤ {RIESGO_PCT_VERDE:.0f}%; ámbar hasta '
                     f'{RIESGO_PCT_AMBAR:.0f}%; rojo por encima'),
            'detalle': f'umbral: {umbral} días'}


def kpi_rotacion(cursor):
    """KPI 3 — Días de cobertura de stock.

    stock total (activos) / promedio diario de unidades vendidas con
    pago confirmado en los últimos 30 días.
    """
    cursor.execute("SELECT COALESCE(SUM(stock), 0) AS s FROM producto"
                   " WHERE activo = TRUE")
    stock = int(cursor.fetchone()['s'])
    cursor.execute("""
        SELECT COALESCE(SUM(d.cantidad), 0) AS u
          FROM detalle_venta d
          JOIN venta v ON v.id_venta = d.id_venta
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
         WHERE v.fecha >= NOW() - INTERVAL '30 days'
    """)
    fila = cursor.fetchone()
    unidades = int(fila['u'] or 0) if fila else 0
    if stock <= 0 or unidades <= 0:
        return {'valor': None, 'texto': '—', 'semaforo': 'sin_datos',
                'formula': 'stock total / promedio diario de unidades vendidas (30 d)',
                'meta': f'verde {ROTACION_DIAS_VERDE_MIN}–{ROTACION_DIAS_VERDE_MAX} d',
                'detalle': 'sin stock o sin ventas pagadas en 30 d'}
    dias = round(stock / (unidades / 30.0), 1)
    return {'valor': dias, 'texto': f'{dias} d', 'semaforo': _semaforo_rotacion(dias),
            'formula': 'stock total / promedio diario de unidades vendidas (30 d)',
            'meta': (f'verde {ROTACION_DIAS_VERDE_MIN}–{ROTACION_DIAS_VERDE_MAX} d; '
                     f'ámbar hasta {ROTACION_DIAS_AMBAR_MAX} d o bajo {ROTACION_DIAS_VERDE_MIN}; '
                     f'rojo < {ROTACION_DIAS_QUIEBRE} d (quiebres) o > {ROTACION_DIAS_AMBAR_MAX} d (capital dormido)'),
            'detalle': f'{unidades} u vendidas en 30 d'}


def kpi_cancelados_online(cursor):
    """KPI 4 — % de pedidos online cancelados o con reserva vencida.

    (canceladas en 30 d + vencidas sin pagar hoy) / online totales 30 d.
    """
    cursor.execute("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE ev.nombre = 'cancelado') AS canceladas
          FROM venta v
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
          JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
         WHERE tv.nombre <> 'local' AND v.fecha >= NOW() - INTERVAL '30 days'
    """)
    fila = cursor.fetchone()
    total, canceladas = int(fila['total']), int(fila['canceladas'])
    params = (_plazo_segundos('tarjeta'), _plazo_segundos('efectivo'),
              _plazo_segundos('tarjeta'), _plazo_segundos('efectivo'))
    cursor.execute("SELECT COUNT(*) AS n FROM (" + _ventas_vencidas_sql() + ") t",
                   params)
    vencidas = int(cursor.fetchone()['n'])
    if total == 0:
        return {'valor': None, 'texto': '—', 'semaforo': 'sin_datos',
                'formula': '(canceladas 30 d + vencidas hoy) / online 30 d × 100',
                'meta': f'verde ≤ {CANCELADOS_PCT_VERDE:.0f}%',
                'detalle': 'sin pedidos online en 30 d'}
    pct = round((canceladas + vencidas) / total * 100.0, 1)
    if pct <= CANCELADOS_PCT_VERDE:
        semaforo = 'verde'
    elif pct <= CANCELADOS_PCT_AMBAR:
        semaforo = 'ambar'
    else:
        semaforo = 'rojo'
    return {'valor': pct, 'texto': f'{pct}%', 'semaforo': semaforo,
            'formula': '(canceladas 30 d + vencidas hoy) / online 30 d × 100',
            'meta': (f'verde ≤ {CANCELADOS_PCT_VERDE:.0f}%; ámbar hasta '
                     f'{CANCELADOS_PCT_AMBAR:.0f}%; rojo por encima'),
            'detalle': f'{canceladas} canceladas + {vencidas} vencidas de {total}'}


def kpi_tiempo_entrega(cursor):
    """KPI 5 — Tiempo medio de entrega (pendiente → entregado).

    Promedio de días entre la creación de la venta online y el cambio a
    'entregado' registrado por el trigger trg_auditar_estado_venta en
    auditoria (con su fecha), sobre cambios de los últimos 30 días.
    """
    cursor.execute(f"""
        SELECT AVG(EXTRACT(EPOCH FROM (a.fecha - v.fecha)) / 86400.0) AS dias
          FROM auditoria a
          JOIN venta v ON v.id_venta = a.id_registro
         WHERE a.tabla_afectada = 'venta'
           AND a.accion = 'cambio_estado'
           AND a.valor_nuevo = (SELECT id_estado_venta FROM estado_venta
                                 WHERE nombre = 'entregado')::TEXT
           AND v.id_tipo_venta = (SELECT id_tipo_venta FROM tipo_venta
                                  WHERE nombre = 'online')
           AND a.fecha >= NOW() - INTERVAL '30 days'
    """)
    fila = cursor.fetchone()
    dias = float(fila['dias']) if fila and fila['dias'] is not None else None
    if dias is None:
        return {'valor': None, 'texto': '—', 'semaforo': 'sin_datos',
                'formula': 'promedio de (fecha del cambio a entregado − fecha de la venta), online',
                'meta': f'verde ≤ {ENTREGA_DIAS_VERDE:.0f} d',
                'detalle': 'sin entregas auditadas en 30 d'}
    dias = round(dias, 1)
    if dias <= ENTREGA_DIAS_VERDE:
        semaforo = 'verde'
    elif dias <= ENTREGA_DIAS_AMBAR:
        semaforo = 'ambar'
    else:
        semaforo = 'rojo'
    return {'valor': dias, 'texto': f'{dias} d', 'semaforo': semaforo,
            'formula': 'promedio de (fecha del cambio a entregado − fecha de la venta), online',
            'meta': (f'verde ≤ {ENTREGA_DIAS_VERDE:.0f} d; ámbar hasta '
                     f'{ENTREGA_DIAS_AMBAR:.0f} d; rojo por encima'),
            'detalle': None}


def kpi_mezcla(cursor):
    """KPI 6 — Mezcla de medio de pago y canal (pagos confirmados 30 d).

    El semáforo mide la concentración de EFECTIVO (riesgo de caja); el
    detalle muestra el desglose por método y por canal.
    """
    cursor.execute(f"""
        SELECT tp.nombre AS metodo,
               COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM pago pg
          JOIN tipo_pago tp ON tp.id_tipo_pago = pg.id_tipo_pago
          JOIN venta v ON v.id_venta = pg.id_venta
          JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE pg.estado = 'confirmado'
           AND v.fecha >= NOW() - INTERVAL '30 days'
         GROUP BY tp.nombre
    """)
    por_metodo = {r['metodo']: float(r['total'] or 0) for r in cursor.fetchall()}
    cursor.execute(f"""
        SELECT tv.nombre AS canal,
               COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
          FROM pago pg
          JOIN venta v ON v.id_venta = pg.id_venta
          JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
          JOIN detalle_venta d ON d.id_venta = v.id_venta
         WHERE pg.estado = 'confirmado'
           AND v.fecha >= NOW() - INTERVAL '30 days'
         GROUP BY tv.nombre
    """)
    por_canal = {r['canal']: float(r['total'] or 0) for r in cursor.fetchall()}
    total = sum(por_metodo.values())
    if total <= 0:
        return {'valor': None, 'texto': '—', 'semaforo': 'sin_datos',
                'formula': 'importe pagado por método y por canal (30 d)',
                'meta': f'efectivo ≤ {EFECTIVO_PCT_VERDE:.0f}%',
                'detalle': 'sin pagos confirmados en 30 d'}
    efectivo_pct = round(por_metodo.get('efectivo', 0.0) / total * 100.0, 1)
    if efectivo_pct <= EFECTIVO_PCT_VERDE:
        semaforo = 'verde'
    elif efectivo_pct <= EFECTIVO_PCT_AMBAR:
        semaforo = 'ambar'
    else:
        semaforo = 'rojo'
    online = por_canal.get('online', 0.0)
    local = por_canal.get('local', 0.0)
    canal = 'sin canal' if online + local <= 0 else \
        f'{round(online / (online + local) * 100)}% online'
    return {'valor': efectivo_pct, 'texto': f'{efectivo_pct}% efectivo',
            'semaforo': semaforo,
            'formula': 'importe pagado por método y por canal (30 d)',
            'meta': (f'efectivo ≤ {EFECTIVO_PCT_VERDE:.0f}% verde; hasta '
                     f'{EFECTIVO_PCT_AMBAR:.0f}% ámbar; > rojo (concentración de caja)'),
            'detalle': canal}
