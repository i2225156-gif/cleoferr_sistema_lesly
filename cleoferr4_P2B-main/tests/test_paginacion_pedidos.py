"""P1: paginación en servidor de /pedidos + KPIs de /reportes estables.

- 45 ventas sintéticas (3 páginas de 20) dentro de una transacción con
  rollback: nada persiste, sin datos personales. Todas con fecha NOW():
  en el ORDER BY (fecha DESC, id DESC) las sintéticas (ids mayores) van
  antes que las reales, así que las páginas son deterministas.
- ?pagina= inválida (abc, 0, -3) → 1; mayor que la última → última (con
  "Mostrando X–Y de Z" correcto), nunca 500.
- Los enlaces conservan los filtros activos (query string sin 'pagina').
- /reportes NO se pagina (no tiene listado largo): sus KPIs renderizados
  son idénticos con y sin ?pagina=.
- El macro con total==0 no pinta nada (nunca "Mostrando 1–0 de 0").
"""
import re
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

import db_supabase
import app as modulo_app
from app import app

_TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'
POR_PAGINA = 20
NPAG = 45   # sintéticas: 3 páginas exactas si no hubiera ventas reales


def _admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


class _Proxy:
    """Contrato de conexión proxied con rollback (como test_admin_layout)."""

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
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = _Proxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(f"""
        INSERT INTO venta (id_tipo_venta, id_estado_venta, num_operacion)
        SELECT (SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
               (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
               'test-p1-' || gs || '-' || md5(random()::text)
        FROM generate_series(1, {NPAG}) AS gs
        RETURNING id_venta
    """)
    ids = sorted(r['id_venta'] for r in cur.fetchall())
    cur.execute("SELECT COUNT(*) AS total FROM venta")
    total = cur.fetchone()['total']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'admin': _admin(), 'ids': ids, 'total': total, 'cur': cur}
    proxy.rollback()
    raw.close()


def _esta(html, pid):
    """El <td class="fw-bold"> de la fila renderiza el id de la venta."""
    return f'>{pid}<' in html


def test_pagina_1_muestra_las_20_mas_recientes(fx):
    html = fx['admin'].get('/pedidos').get_data(as_text=True)
    assert f'Mostrando 1–{POR_PAGINA} de {fx["total"]}' in html
    ids = fx['ids']
    for pid in ids[-POR_PAGINA:]:          # las 20 más nuevas (ids mayores)
        assert _esta(html, pid), f'falta {pid} en página 1'
    assert not _esta(html, ids[-(POR_PAGINA + 1)])   # la 21a va en página 2
    # Accesible y acciones por fila intactas (cambio de estado AJAX)
    assert 'aria-current="page"' in html
    assert 'form-select-estado' in html and 'X-CSRFToken' in html
    assert 'data-pedido-form' in html


def test_pagina_intermedia(fx):
    html = fx['admin'].get('/pedidos?pagina=2').get_data(as_text=True)
    assert f'Mostrando {POR_PAGINA + 1}–{2 * POR_PAGINA} de {fx["total"]}' in html
    ids = fx['ids']
    # Con fecha idéntica (NOW()) el orden es id DESC: la fila de ids[k] es
    # 45-k. Página 2 = filas 21–40 = ids[5]..ids[24].
    assert _esta(html, ids[24])             # primera de la página 2
    assert _esta(html, ids[5])              # última de la página 2
    assert not _esta(html, ids[25])         # fila 20: quedó en la página 1
    assert not _esta(html, ids[4])          # fila 41: va en la página 3
    assert not _esta(html, ids[-1])         # la más nueva quedó en página 1


def test_pagina_invalida_y_clamp(fx):
    c, total = fx['admin'], fx['total']
    for invalida in ('abc', '0', '-3', '1.5'):
        html = c.get(f'/pedidos?pagina={invalida}').get_data(as_text=True)
        assert f'Mostrando 1–{POR_PAGINA} de {total}' in html, invalida
    # Mayor que la última → última página real, con X–Y de Z correcto
    ultima = -(-total // POR_PAGINA)
    html = c.get('/pedidos?pagina=99999').get_data(as_text=True)
    assert f'Mostrando {(ultima - 1) * POR_PAGINA + 1}–{total} de {total}' in html


def test_enlaces_conservan_filtros(fx):
    import html as _htmlmod
    crudo = fx['admin'].get(
        '/pedidos?desde=2026-01-01&estado=pendiente&q=testpag'
    ).get_data(as_text=True)
    # El enlace a la página 2 conserva fecha, estado y búsqueda. En el
    # atributo el & viaja HTML-escapado; se des-escapa para asertar plano.
    plano = _htmlmod.unescape(crudo)
    assert 'desde=2026-01-01&estado=pendiente&q=testpag&pagina=2' in plano
    assert 'pagina=' in crudo            # hay navegación
    assert 'aria-label="Página siguiente"' in crudo


def test_reportes_kpis_estables_con_pagina(fx):
    # /reportes NO se pagina (sin listado largo): sus KPIs no dependen de
    # ?pagina= — los valores renderizados son idénticos con y sin el parámetro
    patron = re.compile(r'<div class="stat-value">([^<]*)</div>')
    r1 = fx['admin'].get('/reportes').get_data(as_text=True)
    r2 = fx['admin'].get('/reportes?pagina=2').get_data(as_text=True)
    kpis1, kpis2 = patron.findall(r1), patron.findall(r2)
    assert kpis1 and kpis1 == kpis2


def test_macro_con_total_cero_no_pinta_nada():
    # Nunca "Mostrando 1–0 de 0": con total == 0 la macro no renderiza nada
    env = Environment(loader=FileSystemLoader(_TEMPLATES),
                      autoescape=select_autoescape())
    macro = env.get_template('components/paginacion.html').module.paginacion
    assert str(macro(1, 0, POR_PAGINA, '')).strip() == ''
    # El estado vacío legible de /pedidos sigue en la plantilla
    fuente = (_TEMPLATES / 'pedidos.html').read_text(encoding='utf-8')
    assert 'No se encontraron pedidos' in fuente
