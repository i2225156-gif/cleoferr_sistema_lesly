"""P4: paginación en servidor de /mis_pedidos y /mis_comprobantes (10 por
página, layout de CLIENTE: base.html, no admin_base).

SEGURIDAD client-scoped: el COUNT, el LIMIT/OFFSET y los totales siguen
filtrados por el cliente de la sesión. Tres clientes sintéticos: A con
25 pedidos/comprobantes, B con 5 y C con ninguno. B nunca ve filas de A
en ninguna página ni en el total; ?pagina=99999 no revela nada ajeno; C
ve su estado vacío aunque A tenga 25.

- La posición de cada fila se deriva en RUNTIME con el MISMO orden de la
  ruta, filtrado por cliente: nada de tamaños hardcodeados.
- Rollback por BD (tienda y auth), sin datos personales.
- ?pagina= inválida (abc, 0, -3, 1.5) → 1; mayor que la última → última.
- El macro de paginación renderiza en el layout de cliente con clases
  Bootstrap (base.html:7) y móvil "Pág. X de Y": sin CSS del sidebar admin.
"""
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app

_TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'
POR_PAGINA = 10
NSINT_A = 25   # cliente A: llena 2 páginas y rueda 5 a la tercera
NSINT_B = 5    # cliente B: una sola página


class ConnProxy:
    """Contrato de conexión proxied con rollback (como P1..P3)."""

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


def _pag_de(posicion):
    """Página (1-index) en la que cae la fila de la posición dada."""
    return (posicion + POR_PAGINA - 1) // POR_PAGINA


def _sesion_cliente(id_cliente):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = id_cliente
        s['rol'] = 'cliente'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    raw_t = db_supabase.engine_tienda.raw_connection()
    proxy_t = ConnProxy(raw_t)
    cur = proxy_t.cursor(dictionary=True)
    raw_a = db_supabase.engine_auth.raw_connection()
    proxy_a = ConnProxy(raw_a)
    cur_a = proxy_a.cursor(dictionary=True)

    # Tres clientes sintéticos en AUTH (A: 25, B: 5, C: ninguno)
    cur_a.execute("""
        INSERT INTO cliente (nombre, apellido, id_tipo_documento,
                             nro_documento, activo, origen)
        VALUES ('testp4-cli-a', 'prueba', 1, right(md5(random()::text), 8), TRUE, 'pos'),
               ('testp4-cli-b', 'prueba', 1, right(md5(random()::text), 8), TRUE, 'pos'),
               ('testp4-cli-c', 'prueba', 1, right(md5(random()::text), 8), TRUE, 'pos')
        RETURNING id_cliente, nombre
    """)
    filas = cur_a.fetchall()
    cli = {r['nombre'].rsplit('-', 1)[-1]: r['id_cliente'] for r in filas}
    cli_a, cli_b, cli_c = cli['a'], cli['b'], cli['c']

    # Ventas sintéticas en TIENDA por cliente (fechas distintas y recientes)
    def _pedidos(id_cliente, n):
        cur.execute("""
            INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta,
                               num_operacion, fecha)
            SELECT (SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'), %s,
                   (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
                   'testp4-ped-' || gs || '-' || md5(random()::text),
                   NOW() - (gs || ' minutes')::interval
              FROM generate_series(1, %s) AS gs
            RETURNING id_venta
        """, (id_cliente, n))
        return sorted(r['id_venta'] for r in cur.fetchall())

    ids_a = _pedidos(cli_a, NSINT_A)
    ids_b = _pedidos(cli_b, NSINT_B)

    # Un comprobante por venta (serie+número únicos; envío por ordinalidad)
    cur.execute("SELECT id_tipo_comprobante FROM tipo_comprobante"
                " ORDER BY id_tipo_comprobante LIMIT 1")
    id_tipo = cur.fetchone()['id_tipo_comprobante']
    cur.execute("""
        INSERT INTO comprobante (id_venta, id_tipo_comprobante, numero, serie,
                                 enviado_correo, fecha_emision, estado_sunat,
                                 subtotal, igv, total)
        SELECT x.id_venta, %s,
               'B' || lpad(x.gs::text, 6, '0'), 'B999', FALSE,
               NOW() - (x.gs || ' minutes')::interval, 'enviado',
               10.00, 1.69, 11.69
          FROM unnest(%s) WITH ORDINALITY AS x(id_venta, gs)
    """, (id_tipo, ids_a + ids_b))

    # Posiciones en RUNTIME con el MISMO orden de cada ruta, por cliente
    pos_ped, pos_comp, marcas_comp = {}, {}, {}
    for cli_id in (cli_a, cli_b):
        cur.execute("SELECT id_venta FROM venta WHERE id_cliente = %s"
                    " ORDER BY fecha DESC, id_venta DESC", (cli_id,))
        pos_ped[cli_id] = {r['id_venta']: i + 1
                           for i, r in enumerate(cur.fetchall())}
        cur.execute("""SELECT c.serie, c.numero, c.id_venta
                        FROM comprobante c JOIN venta v ON v.id_venta = c.id_venta
                       WHERE v.id_cliente = %s
                       ORDER BY c.fecha_emision DESC, c.id_comprobante DESC""",
                    (cli_id,))
        filas = cur.fetchall()
        pos_comp[cli_id] = {f['id_venta']: i + 1 for i, f in enumerate(filas)}
        marcas_comp[cli_id] = {f['id_venta']: f"{f['serie']}-{f['numero'][-6:]}"
                               for f in filas}

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy_t)
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy_a)
    yield {'a': _sesion_cliente(cli_a), 'b': _sesion_cliente(cli_b),
           'c': _sesion_cliente(cli_c), 'cli_a': cli_a, 'cli_b': cli_b,
           'ids_a': ids_a, 'ids_b': ids_b, 'pos_ped': pos_ped,
           'pos_comp': pos_comp, 'marcas_comp': marcas_comp}
    proxy_t.rollback()
    raw_t.close()
    proxy_a.rollback()
    raw_a.close()


# ── /mis_pedidos (orden: fecha DESC, id DESC) ─────────────────────

def test_mis_pedidos_pagina_1_y_total_del_cliente(fx):
    pos = fx['pos_ped'][fx['cli_a']]
    html = fx['a'].get('/mis_pedidos').get_data(as_text=True)
    # total y filas son SOLO del cliente A: sus 25 sintéticos
    assert f'Mostrando 1–{POR_PAGINA} de {NSINT_A}' in html
    assert 'aria-current="page"' in html
    # el macro renderiza en el layout de CLIENTE: clases Bootstrap
    # (viajan en base.html) y móvil "Pág. X de Y", sin CSS del admin
    assert 'aria-label="Paginación de resultados"' in html
    assert 'pagination pagination-sm' in html
    assert 'Pág. 1 de 3' in html
    assert 'href="?pagina=2"' in html
    for vid in fx['ids_a']:
        assert (f'#{vid}<' in html) == (_pag_de(pos[vid]) == 1), vid
    for vid in fx['ids_b']:
        assert f'#{vid}<' not in html          # NADA del cliente B


def test_mis_pedidos_clamp_y_99999_sin_fugas(fx):
    pos = fx['pos_ped'][fx['cli_a']]
    c = fx['a']
    for pagina in (2, 3):
        html = c.get(f'/mis_pedidos?pagina={pagina}').get_data(as_text=True)
        for vid in fx['ids_a']:
            assert (f'#{vid}<' in html) == (_pag_de(pos[vid]) == pagina), (pagina, vid)
        for vid in fx['ids_b']:
            assert f'#{vid}<' not in html, pagina
    html = c.get('/mis_pedidos?pagina=99999').get_data(as_text=True)
    assert f'Mostrando {2 * POR_PAGINA + 1}–{NSINT_A} de {NSINT_A}' in html
    for vid in fx['ids_a']:
        assert (f'#{vid}<' in html) == (_pag_de(pos[vid]) == 3), vid
    for vid in fx['ids_b']:
        assert f'#{vid}<' not in html          # el clamp no revela nada ajeno


def test_mis_pedidos_cliente_b_ve_solo_sus_5(fx):
    html = fx['b'].get('/mis_pedidos').get_data(as_text=True)
    assert f'Mostrando 1–{NSINT_B} de {NSINT_B}' in html
    for vid in fx['ids_b']:
        assert f'#{vid}<' in html
    for vid in fx['ids_a']:
        assert f'#{vid}<' not in html
    # clamp con una sola página: 99999 cae a la 1, sin nada de A
    html = fx['b'].get('/mis_pedidos?pagina=99999').get_data(as_text=True)
    assert f'Mostrando 1–{NSINT_B} de {NSINT_B}' in html
    for vid in fx['ids_a']:
        assert f'#{vid}<' not in html


def test_mis_pedidos_pagina_invalida(fx):
    c = fx['a']
    for invalida in ('abc', '0', '-3', '1.5'):
        html = c.get(f'/mis_pedidos?pagina={invalida}').get_data(as_text=True)
        assert f'Mostrando 1–{POR_PAGINA} de {NSINT_A}' in html, invalida


def test_mis_pedidos_cliente_sin_pedidos_ve_estado_vacio(fx):
    # C no tiene pedidos PROPIOS aunque A tenga 25: vacío client-scoped
    # y macro oculta con total 0
    html = fx['c'].get('/mis_pedidos').get_data(as_text=True)
    assert 'Aún no tienes pedidos' in html
    assert 'Mostrando' not in html
    for vid in fx['ids_a']:
        assert f'#{vid}<' not in html


# ── /mis_comprobantes (orden: fecha_emision DESC, id DESC) ────────

def test_mis_comprobantes_pagina_1_y_total_del_cliente(fx):
    pos = fx['pos_comp'][fx['cli_a']]
    marcas = fx['marcas_comp'][fx['cli_a']]
    html = fx['a'].get('/mis_comprobantes').get_data(as_text=True)
    assert f'Mostrando 1–{POR_PAGINA} de {NSINT_A}' in html
    assert 'aria-label="Paginación de resultados"' in html
    for vid, marca in marcas.items():
        assert (f'>{marca}<' in html) == (_pag_de(pos[vid]) == 1), marca
        if _pag_de(pos[vid]) == 1:
            assert f'/mi_comprobante/{vid}' in html       # acción por fila
    for marca in fx['marcas_comp'][fx['cli_b']].values():
        assert f'>{marca}<' not in html          # NADA del cliente B


def test_mis_comprobantes_clamp_y_99999_sin_fugas(fx):
    pos = fx['pos_comp'][fx['cli_a']]
    marcas = fx['marcas_comp'][fx['cli_a']]
    html = fx['a'].get('/mis_comprobantes?pagina=99999').get_data(as_text=True)
    assert f'Mostrando {2 * POR_PAGINA + 1}–{NSINT_A} de {NSINT_A}' in html
    for vid, marca in marcas.items():
        assert (f'>{marca}<' in html) == (_pag_de(pos[vid]) == 3), marca
    for marca in fx['marcas_comp'][fx['cli_b']].values():
        assert f'>{marca}<' not in html          # el clamp no revela nada ajeno


def test_mis_comprobantes_cliente_b_ve_solo_sus_5(fx):
    html = fx['b'].get('/mis_comprobantes').get_data(as_text=True)
    assert f'Mostrando 1–{NSINT_B} de {NSINT_B}' in html
    for marca in fx['marcas_comp'][fx['cli_b']].values():
        assert f'>{marca}<' in html
    for marca in fx['marcas_comp'][fx['cli_a']].values():
        assert f'>{marca}<' not in html


def test_mis_comprobantes_pagina_invalida(fx):
    c = fx['a']
    for invalida in ('abc', '0', '-3', '1.5'):
        html = c.get(f'/mis_comprobantes?pagina={invalida}').get_data(as_text=True)
        assert f'Mostrando 1–{POR_PAGINA} de {NSINT_A}' in html, invalida


def test_mis_comprobantes_cliente_sin_comprobantes_ve_estado_vacio(fx):
    html = fx['c'].get('/mis_comprobantes').get_data(as_text=True)
    assert 'Aún no tienes comprobantes' in html
    assert 'Mostrando' not in html
    for marca in fx['marcas_comp'][fx['cli_a']].values():
        assert f'>{marca}<' not in html
