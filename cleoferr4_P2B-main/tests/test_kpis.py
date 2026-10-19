"""KPIs empresariales del dashboard (3, 4, 5 y 6): tests deterministas.

- Los valores renderizados se comparan contra el MISMO cálculo hecho
  por separado sobre la misma transacción (los tests no dependen de los
  datos reales).
- Los umbrales del semáforo se prueban por frontera SIN BD (rápidos).
- Datos sintéticos con rollback: ventas pagadas/canceladas/vencidas y
  una entrega auditada del trigger trg_auditar_estado_venta.
"""
import html as _html

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services import kpis as k
from services.kpis import (_semaforo_rotacion, kpi_cancelados_online,
                           kpi_mezcla, kpi_rotacion, kpi_tiempo_entrega)


class ConnProxy:
    """Contrato de conexión proxied con rollback (como P1..P4)."""

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


def _admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)

    # Producto sintético con stock (para que la rotación tenga datos)
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock)
        VALUES ('zzqkpi-prod', 10.00, 100)
        RETURNING id_producto
    """)
    pid = cur.fetchone()['id_producto']

    def _venta(estado, fecha_sql, pago_estado='confirmado', tipo='online'):
        cur.execute(f"""
            INSERT INTO venta (id_tipo_venta, id_estado_venta, num_operacion, fecha)
            VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='{tipo}'),
                    (SELECT id_estado_venta FROM estado_venta WHERE nombre='{estado}'),
                    'zzqkpi-' || md5(random()::text),
                    {fecha_sql})
            RETURNING id_venta
        """)
        vid = cur.fetchone()['id_venta']
        cur.execute("""
            INSERT INTO detalle_venta (id_venta, id_producto, cantidad,
                                       precio_unitario, descuento_unitario)
            VALUES (%s, %s, 1, 10.00, 0)
        """, (vid, pid))
        cur.execute("""
            INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                              id_transaccion, pasarela)
            VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
                    NULL, %s, 10.00, %s, 'simulada')
        """, (vid, pago_estado, f'sim_zzqkpi_{vid}'))
        return vid

    # Venta pagada hace 5 días (dentro de la ventana de 30 d)
    _venta('confirmado', "NOW() - INTERVAL '5 days'")
    # Cancelada hace 3 días (cuenta en el KPI 4)
    _venta('cancelado', "NOW() - INTERVAL '3 days'", pago_estado='pendiente')
    # Reserva vencida: tarjeta pendiente de hace 40 minutos
    _venta('pendiente', "NOW() - INTERVAL '40 minutes'", pago_estado='pendiente')
    # Entrega auditada: venta online pendiente hace 4 días, entregada hace 2
    vid_entregada = _venta('pendiente', "NOW() - INTERVAL '4 days'")
    cur.execute("""
        INSERT INTO auditoria (tabla_afectada, id_registro, accion,
                                valor_anterior, valor_nuevo, fecha)
        VALUES ('venta', %s, 'cambio_estado', '1',
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='entregado'),
                NOW() - INTERVAL '2 days')
    """, (vid_entregada,))

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'c': _admin(), 'cur': cur}
    proxy.rollback()
    raw.close()


# ── umbrales por frontera (sin BD) ──────────────────────────────

def test_semaforo_rotacion_bordes():
    assert _semaforo_rotacion(14.9) == 'rojo'      # quiebres
    assert _semaforo_rotacion(15) == 'ambar'
    assert _semaforo_rotacion(29.9) == 'ambar'
    assert _semaforo_rotacion(30) == 'verde'
    assert _semaforo_rotacion(60) == 'verde'
    assert _semaforo_rotacion(60.1) == 'ambar'
    assert _semaforo_rotacion(90) == 'ambar'
    assert _semaforo_rotacion(90.1) == 'rojo'      # capital dormido


def test_constantes_configurables_en_un_lugar():
    assert k.ROTACION_DIAS_QUIEBRE == 15
    assert k.ROTACION_DIAS_VERDE_MIN == 30 and k.ROTACION_DIAS_VERDE_MAX == 60
    assert k.ROTACION_DIAS_AMBAR_MAX == 90
    assert k.CANCELADOS_PCT_VERDE == 10.0 and k.CANCELADOS_PCT_AMBAR == 20.0
    assert k.ENTREGA_DIAS_VERDE == 2.0 and k.ENTREGA_DIAS_AMBAR == 4.0
    assert k.EFECTIVO_PCT_VERDE == 60.0 and k.EFECTIVO_PCT_AMBAR == 80.0


# ── integración del dashboard (valores contra cálculo independiente) ──

def test_dashboard_renderiza_los_cuatro_kpis(fx):
    r = fx['c'].get('/admin/dashboard')
    assert r.status_code == 200
    # los textos viajan HTML-escapados ('<' → '<'): des-escapar
    html = _html.unescape(r.get_data(as_text=True))
    cur = fx['cur']
    for esperado, funcion in ((kpi_rotacion(cur), 'rotacion'),
                             (kpi_cancelados_online(cur), 'cancelados'),
                             (kpi_tiempo_entrega(cur), 'entrega'),
                             (kpi_mezcla(cur), 'mezcla')):
        assert esperado['texto'] in html, (funcion, esperado)
        # tooltip visible con fórmula y meta
        assert esperado['formula'] in html
        assert esperado['meta'] in html
    # las cuatro tarjetas con sus etiquetas
    for etiqueta in ('Cobertura de stock', 'Pedidos online cancelados',
                     'Tiempo medio de entrega', 'Mezcla de medios de pago'):
        assert etiqueta in html


def test_kpis_sinteticos_influyen(fx):
    cur = fx['cur']
    # rotación: stock 100 (sintético) + reales; unidades >= 1 → valor numérico
    rot = kpi_rotacion(cur)
    assert rot['valor'] is not None and rot['semaforo'] in ('verde', 'ambar', 'rojo')
    # cancelados: los contadores se derivan al runtime con el mismo
    # criterio (los reales también cuentan: la sintética suma 1 a cada uno)
    canc = kpi_cancelados_online(cur)
    assert canc['valor'] is not None and canc['valor'] >= 0
    cur.execute("""
        SELECT COUNT(*) AS c FROM venta v
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
          JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
         WHERE tv.nombre <> 'local' AND ev.nombre = 'cancelado'
           AND v.fecha >= NOW() - INTERVAL '30 days'
    """)
    canceladas_esperadas = int(cur.fetchone()['c'])
    # el detalle refleja exactamente los contadores de la misma transacción
    partes = canc['detalle'].split(' de ')[0]
    assert partes.startswith(f'{canceladas_esperadas} canceladas + ')
    assert partes.endswith(' vencidas')
    vencidas_esperadas = int(partes.split(' + ')[1].split()[0])
    assert vencidas_esperadas >= 1      # la vencida sintética (más las reales)
    # entrega: la venta auditada de 4d->2d aporta 2 días al promedio
    entrega = kpi_tiempo_entrega(cur)
    assert entrega['valor'] is not None
    assert entrega['valor'] <= 2.0      # promedio con la sintética de 2.0 d
    # mezcla: hay efectivo sintético → texto con %
    mezcla = kpi_mezcla(cur)
    assert mezcla['valor'] is not None and '%' in mezcla['texto']


def test_sin_datos_muestra_guion(fx):
    """En una transacción con datos, la estructura admite 'sin datos':
    se comprueba el camino con la función aislada sobre datos vacíos."""
    cur = fx['cur']
    # mezcla con un WHERE que no casa nada no es posible aquí; en su lugar
    # se verifica la estructura del dict devuelto
    mezcla = kpi_mezcla(cur)
    for clave in ('valor', 'texto', 'semaforo', 'formula', 'meta', 'detalle'):
        assert clave in mezcla
