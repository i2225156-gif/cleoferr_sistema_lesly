"""P3: paginación en servidor de /admin/reservas y /admin/pagos_simulados.

- 25 sintéticos por listado dentro de una transacción con rollback (BD
  tienda), sin datos personales.
- La posición de cada fila se deriva en RUNTIME consultando el mismo
  orden del listado: los tests no dependen de cuántas reales haya ni
  de que esas no cambien.
- /admin/reservas: el número del aviso/botón/data-confirm es el TOTAL
  del conjunto completo (no el de la página) y el POST libera TODAS
  las vencidas — incluidas las de páginas > 1 —, exactamente las mismas
  que liberaba antes de paginar (liberar_reservas_vencidas intacta).
- /admin/pagos_simulados: el selector de pendientes (tope 50) y el POST
  disparar quedan intactos; el historial ya no se recorta a 30 fijos.
- ?pagina= inválida (abc, 0, -3, 1.5) → 1; mayor que la última → última
  con "Mostrando X–Y de Z" correcto, nunca 500.
"""
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.reservas import _plazo_segundos, _ventas_vencidas_sql

_TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'
POR_PAGINA = 20
NSINT = 25   # sintéticos: llena una página y rueda el resto a la siguiente
MARCADOR_EVENTO = 'evt-testp3-'


class ConnProxy:
    """Contrato de conexión proxied con rollback (como P1/P2).

    commit() es no-op: lo que escribe la ruta queda en la transacción y
    se revierte con proxy.rollback() en el teardown del fixture.
    """

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


def _pag_de(posicion):
    """Página (1-index) en la que cae la fila de la posición dada."""
    return (posicion + POR_PAGINA - 1) // POR_PAGINA


def _params_plazos():
    return (_plazo_segundos('tarjeta'), _plazo_segundos('efectivo'),
            _plazo_segundos('tarjeta'), _plazo_segundos('efectivo'))


@pytest.fixture()
def fx(monkeypatch):
    # Plazos FIJOS para que el criterio de "vencida" sea determinista,
    # igual que tests/test_reservas_vencidas.py.
    monkeypatch.setenv('RESERVA_PLAZO_TARJETA_SEG', str(30 * 60))
    monkeypatch.setenv('RESERVA_PLAZO_OPERACION_SEG', str(24 * 3600))
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)

    # 25 reservas vencidas sintéticas (tarjeta con 31 min de antigüedad):
    # venta pendiente online + pago pendiente. Sin detalle_venta: la
    # devolución de stock es no-op para ellas y la lógica de liberación
    # no se toca.
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta,
                           num_operacion, fecha)
        SELECT (SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
               11,
               (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
               'testp3-res-' || gs || '-' || md5(random()::text),
               NOW() - make_interval(secs => 31 * 60 + gs)
          FROM generate_series(1, %s) AS gs
        RETURNING id_venta
    """, (NSINT,))
    ids_reservas = sorted(r['id_venta'] for r in cur.fetchall())
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        SELECT x.id_venta,
               (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='tarjeta'),
               NULL, 'pendiente', 10.00,
               'sim_testp3_' || x.id_venta::text, 'simulada'
          FROM unnest(%s) AS x(id_venta)
    """, (ids_reservas,))

    # 25 eventos sintéticos: recibido_en distinto por fila (recientes).
    cur.execute(f"""
        INSERT INTO pago_evento (proveedor, id_evento_proveedor, tipo,
                                  estado_resultante, firma_valida,
                                  dentro_de_tolerancia, recibido_en)
        SELECT 'simulada',
               '{MARCADOR_EVENTO}' || lpad(gs::text, 2, '0') || '-' ||
               md5(random()::text),
               'pago_actualizado', 'confirmado', TRUE, TRUE,
               NOW() - (gs || ' minutes')::interval
          FROM generate_series(1, %s) AS gs
    """, (NSINT,))

    # 2 pagos pendientes sintéticos para el selector (tope 50, sin paginar)
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta,
                           num_operacion)
        SELECT (SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
               NULL,
               (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
               'testp3-pen-' || gs || '-' || md5(random()::text)
          FROM generate_series(1, 2) AS gs
        RETURNING id_venta
    """)
    ids_pen = [r['id_venta'] for r in cur.fetchall()]
    cur.execute("""
        INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, monto,
                          id_transaccion, pasarela)
        SELECT x.id_venta,
               (SELECT id_tipo_pago FROM tipo_pago WHERE nombre='efectivo'),
               NULL, 'pendiente', 10.00,
               'sim_pen_' || x.id_venta::text, 'simulada'
          FROM unnest(%s) AS x(id_venta)
        RETURNING id_pago
    """, (ids_pen,))
    ids_pagos_pen = [r['id_pago'] for r in cur.fetchall()]

    # Posiciones en RUNTIME con el MISMO orden de cada listado.
    cur.execute(_ventas_vencidas_sql() +
                " ORDER BY v.fecha DESC, v.id_venta DESC", _params_plazos())
    orden_reservas = [r['id_venta'] for r in cur.fetchall()]
    pos_reservas = {vid: i + 1 for i, vid in enumerate(orden_reservas)}
    cur.execute("""SELECT id_evento_proveedor FROM pago_evento
                    ORDER BY recibido_en DESC, id_pago_evento DESC""")
    orden_eventos = [r['id_evento_proveedor'] for r in cur.fetchall()]
    pos_eventos = {m: i + 1 for i, m in enumerate(orden_eventos)}

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'admin': _admin(), 'cur': cur, 'proxy': proxy,
           'ids_reservas': ids_reservas, 'ids_pagos_pen': ids_pagos_pen,
           'total_reservas': len(orden_reservas), 'pos_reservas': pos_reservas,
           'total_eventos': len(orden_eventos), 'pos_eventos': pos_eventos}
    proxy.rollback()
    raw.close()


def _eventos_sinteticos_en_pagina(fx, pagina):
    return sum(1 for m, pos in fx['pos_eventos'].items()
               if m.startswith(MARCADOR_EVENTO) and _pag_de(pos) == pagina)


# ── /admin/reservas (orden: fecha DESC, id DESC) ───────────────────

def test_reservas_pagina_1_con_total_del_conjunto(fx):
    total = fx['total_reservas']
    html = fx['admin'].get('/admin/reservas').get_data(as_text=True)
    assert f'Mostrando 1–{POR_PAGINA} de {total}' in html
    assert 'aria-current="page"' in html
    # el número del aviso, del data-confirm y del botón es el TOTAL del
    # conjunto completo, no el de la página (total ≥ 25 > 20)
    assert f'Vista previa: {total} reserva(s) vencida(s) que se liberarán' in html
    assert f'data-confirm="¿Liberar {total} reserva(s)?' in html
    assert f'Liberar {total} reserva(s) vencida(s)' in html
    assert total > POR_PAGINA
    # la tabla muestra SOLO la página: 20 filas, cada sintética en la
    # página que le toca por posición (derivada en runtime)
    assert html.count('<td>#') == POR_PAGINA
    for vid in fx['ids_reservas']:
        assert (f'#{vid}<' in html) == (_pag_de(fx['pos_reservas'][vid]) == 1), vid


def test_reservas_pagina_2_y_ultima_por_clamp(fx):
    total = fx['total_reservas']
    c = fx['admin']
    html = c.get('/admin/reservas?pagina=2').get_data(as_text=True)
    fin = min(2 * POR_PAGINA, total)
    assert f'Mostrando {POR_PAGINA + 1}–{fin} de {total}' in html
    for vid in fx['ids_reservas']:
        assert (f'#{vid}<' in html) == (_pag_de(fx['pos_reservas'][vid]) == 2), vid
    # mayor que la última → clamp a la última; el TOTAL sigue en el botón
    ultima = _pag_de(total)
    off = (ultima - 1) * POR_PAGINA
    html = c.get('/admin/reservas?pagina=99999').get_data(as_text=True)
    assert f'Mostrando {off + 1}–{total} de {total}' in html
    assert f'Vista previa: {total} reserva(s)' in html
    for vid in fx['ids_reservas']:
        assert (f'#{vid}<' in html) == (_pag_de(fx['pos_reservas'][vid]) == ultima), vid


def test_reservas_pagina_invalida(fx):
    total = fx['total_reservas']
    c = fx['admin']
    for invalida in ('abc', '0', '-3', '1.5'):
        html = c.get(f'/admin/reservas?pagina={invalida}').get_data(as_text=True)
        assert f'Mostrando 1–{POR_PAGINA} de {total}' in html, invalida


def test_reservas_hrefs_sin_filtros(fx):
    html = fx['admin'].get('/admin/reservas').get_data(as_text=True)
    assert 'aria-label="Paginación de resultados"' in html
    assert 'href="?pagina=2"' in html      # sin filtros: querystring pelado


def test_reservas_estado_vacio_en_plantilla():
    fuente = (_TEMPLATES / 'admin_reservas.html').read_text(encoding='utf-8')
    assert 'No hay reservas vencidas en este momento' in fuente


def test_reservas_liberar_opera_sobre_el_total(fx):
    """El POST libera TODAS las vencidas (conjunto completo), incluidas
    las de páginas > 1: exactamente las mismas que liberaba antes de
    paginar. La lógica de liberación no se toca."""
    cur, total = fx['cur'], fx['total_reservas']
    r = fx['admin'].post('/admin/reservas/liberar',
                         data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    html = fx['admin'].get('/admin/reservas').get_data(as_text=True)
    assert f'Se liberaron {total} reservas' in html
    # las 25 sintéticas — incluidas las de páginas > 1 — quedaron
    # canceladas (venta y pago)
    cur.execute("""
        SELECT COUNT(*) AS n FROM venta v
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
         WHERE v.id_venta = ANY(%s) AND ev.nombre = 'cancelado'
    """, (fx['ids_reservas'],))
    assert cur.fetchone()['n'] == NSINT
    cur.execute("""SELECT COUNT(*) AS n FROM pago
                    WHERE id_venta = ANY(%s) AND estado = 'cancelado'""",
                (fx['ids_reservas'],))
    assert cur.fetchone()['n'] == NSINT
    # ya no queda ninguna vencida: se liberó el conjunto COMPLETO
    cur.execute("SELECT COUNT(*) AS n FROM (" + _ventas_vencidas_sql() + ") t",
                _params_plazos())
    assert cur.fetchone()['n'] == 0
    # y el panel muestra su estado vacío
    assert 'No hay reservas vencidas en este momento' in html
    # idempotente (comportamiento previo intacto): ya no hay nada que liberar
    fx['admin'].post('/admin/reservas/liberar', data={'csrf_token': 'token-test'})
    html = fx['admin'].get('/admin/reservas').get_data(as_text=True)
    assert 'No había reservas vencidas que liberar' in html


# ── /admin/pagos_simulados (orden: recibido_en DESC, id DESC) ──────

def test_pagos_pagina_1(fx):
    total = fx['total_eventos']
    html = fx['admin'].get('/admin/pagos_simulados').get_data(as_text=True)
    assert f'Mostrando 1–{POR_PAGINA} de {total}' in html
    assert 'Últimos eventos' in html
    assert 'máx. 30' not in html          # el tope fijo se fue con la paginación
    assert html.count(MARCADOR_EVENTO) == _eventos_sinteticos_en_pagina(fx, 1)
    # el selector de pendientes (tope 50, sin paginar) sigue en la página
    assert '<select name="id_pago"' in html
    for id_pago in fx['ids_pagos_pen']:
        assert f'value="{id_pago}"' in html


def test_pagos_pagina_2_y_ultima_por_clamp(fx):
    total = fx['total_eventos']
    c = fx['admin']
    html = c.get('/admin/pagos_simulados?pagina=2').get_data(as_text=True)
    fin = min(2 * POR_PAGINA, total)
    assert f'Mostrando {POR_PAGINA + 1}–{fin} de {total}' in html
    assert html.count(MARCADOR_EVENTO) == _eventos_sinteticos_en_pagina(fx, 2)
    # el selector sigue presente en páginas > 1
    for id_pago in fx['ids_pagos_pen']:
        assert f'value="{id_pago}"' in html
    ultima = _pag_de(total)
    off = (ultima - 1) * POR_PAGINA
    html = c.get('/admin/pagos_simulados?pagina=99999').get_data(as_text=True)
    assert f'Mostrando {off + 1}–{total} de {total}' in html
    assert html.count(MARCADOR_EVENTO) == _eventos_sinteticos_en_pagina(fx, ultima)


def test_pagos_pagina_invalida(fx):
    total = fx['total_eventos']
    c = fx['admin']
    for invalida in ('abc', '0', '-3', '1.5'):
        html = c.get(f'/admin/pagos_simulados?pagina={invalida}'
                     ).get_data(as_text=True)
        assert f'Mostrando 1–{POR_PAGINA} de {total}' in html, invalida


def test_pagos_hrefs_sin_filtros(fx):
    html = fx['admin'].get('/admin/pagos_simulados').get_data(as_text=True)
    assert 'href="?pagina=2"' in html


def test_pagos_estado_vacio_en_plantilla():
    fuente = (_TEMPLATES / 'admin_pagos_simulados.html').read_text(encoding='utf-8')
    assert 'Todavía no hay eventos de pago registrados' in fuente
