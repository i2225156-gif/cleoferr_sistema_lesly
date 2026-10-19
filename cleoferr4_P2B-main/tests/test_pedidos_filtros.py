"""RF9: filtros de /pedidos aplicados en servidor (desde/hasta/estado/q).

- Fechas en días calendario Lima (services/horario); estado validado contra
  estado_venta; q casa nombre+apellido del cliente (BD Auth) o el código de
  venta si es todo dígitos. Consultas parametrizadas; entradas inválidas se
  ignoran con un aviso (nunca 500). La paginación (P1) se conserva y los
  href llevan los filtros.
- El conjunto esperado se deriva en RUNTIME con la misma semántica: los
  tests no dependen de cuántas ventas reales haya.
- Incluye la opción (b) de RF9: el formulario de /reportes/generar queda
  deshabilitado con nota visible (apuntaba a una ruta inexistente).
"""
import html as _html
from datetime import date

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.horario import SQL_DIA_LIMA

POR_PAGINA = 20
MARCA = 'zzqtestpf'


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


def _vendedor():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
        s['nombre'] = 'Prueba'
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

    cur_a.execute("""
        INSERT INTO cliente (nombre, apellido, id_tipo_documento,
                             nro_documento, activo, origen)
        VALUES (%s, 'Cliente', 1, right(md5(random()::text), 8), TRUE, 'pos')
        RETURNING id_cliente
    """, ('Zzqtestpf',))
    cli = cur_a.fetchone()['id_cliente']

    def _venta(fecha_sql, estado):
        cur.execute(f"""
            INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta,
                               num_operacion, fecha)
            VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                    %s,
                    (SELECT id_estado_venta FROM estado_venta WHERE nombre=%s),
                    'testrf9-' || md5(random()::text),
                    {fecha_sql})
            RETURNING id_venta
        """, (cli, estado))
        return cur.fetchone()['id_venta']

    v_dentro = _venta("'2026-10-15 10:00:00-05'::timestamptz", 'pendiente')
    v_fuera = _venta("'2026-10-05 10:00:00-05'::timestamptz", 'pendiente')
    v_canc = _venta("'2026-10-15 11:00:00-05'::timestamptz", 'cancelado')
    # 25 ventas dentro del rango con minutos distintos (orden estable)
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta,
                           num_operacion, fecha)
        SELECT (SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
               %s,
               (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
               'testrf9-b' || gs || '-' || md5(random()::text),
               '2026-10-15 13:00:00-05'::timestamptz + make_interval(secs => gs * 60)
          FROM generate_series(1, 25) AS gs
        RETURNING id_venta
    """, (cli,))
    lote = sorted(r['id_venta'] for r in cur.fetchall())

    cur_a.execute("SELECT id_cliente, nombre, apellido FROM cliente")
    clientes = {r['id_cliente']: (r['nombre'] or '', r['apellido'] or '')
               for r in cur_a.fetchall()}

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy_t)
    monkeypatch.setattr(modulo_app, 'get_connection_auth', lambda: proxy_a)
    yield {'c': _vendedor(), 'cur': cur, 'cli': cli, 'clientes': clientes,
           'v_dentro': v_dentro, 'v_fuera': v_fuera, 'v_canc': v_canc,
           'lote': lote}
    proxy_t.rollback()
    raw_t.close()
    proxy_a.rollback()
    raw_a.close()


def _ids_esperados(fx, desde=None, hasta=None, estado=None, q=None):
    """Misma semántica del filtro de la ruta, calculada por separado."""
    condiciones, params = [], []
    if desde:
        condiciones.append(f"{SQL_DIA_LIMA.format(p='v.', col='fecha')} >= %s")
        params.append(date.fromisoformat(desde))
    if hasta:
        condiciones.append(f"{SQL_DIA_LIMA.format(p='v.', col='fecha')} <= %s")
        params.append(date.fromisoformat(hasta))
    if estado:
        condiciones.append("ev.nombre = %s")
        params.append(estado)
    if q:
        cids = [cid for cid, (nom, ape) in fx['clientes'].items()
                if q.lower() in f"{nom} {ape}".lower()]
        if q.isdigit():
            condiciones.append("(v.id_cliente = ANY(%s) OR v.id_venta = %s)")
            params += [cids, int(q)]
        elif cids:
            condiciones.append("v.id_cliente = ANY(%s)")
            params.append(cids)
        else:
            condiciones.append("FALSE")
    cur = fx['cur']
    cur.execute("SELECT v.id_venta FROM venta v"
                " JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta"
                + (" WHERE " + " AND ".join(condiciones) if condiciones else "")
                + " ORDER BY v.fecha DESC, v.id_venta DESC", params)
    return [r['id_venta'] for r in cur.fetchall()]


def _pag_de(pos):
    return (pos + POR_PAGINA - 1) // POR_PAGINA


def _revisar(fx, querystring, **filtros):
    """GET con filtros y aserción completa de página 1 y total."""
    html = fx['c'].get(f'/pedidos{querystring}').get_data(as_text=True)
    esperados = _ids_esperados(fx, **filtros)
    fin = min(POR_PAGINA, len(esperados))
    assert f'Mostrando 1–{fin} de {len(esperados)}' in html
    for pos, vid in enumerate(esperados, start=1):
        assert (f'>{vid}</td>' in html) == (_pag_de(pos) == 1), vid
    return html


def test_rango_de_fechas_lima(fx):
    # solo el 15/10: v_dentro, v_canc y el lote; v_fuera (05/10) queda fuera
    html = _revisar(fx, '?desde=2026-10-14&hasta=2026-10-16',
                    desde='2026-10-14', hasta='2026-10-16')
    assert f'>{fx["v_fuera"]}</td>' not in html
    # el lote llena la página 1; v_canc y v_dentro van en la 2
    html2 = fx['c'].get('/pedidos?desde=2026-10-14&hasta=2026-10-16&pagina=2'
                        ).get_data(as_text=True)
    assert f'>{fx["v_canc"]}</td>' in html2
    assert f'>{fx["v_dentro"]}</td>' in html2


def test_por_estado(fx):
    _revisar(fx, '?estado=cancelado', estado='cancelado')


def test_por_cliente(fx):
    # q por nombre: las 28 del cliente sintético (lote + 3 sueltas)
    _revisar(fx, f'?q={MARCA}', q=MARCA)


def test_por_codigo_de_venta(fx):
    _revisar(fx, f'?q={fx["v_dentro"]}', q=str(fx['v_dentro']))


def test_rango_y_estado_combinados(fx):
    html = _revisar(fx, '?desde=2026-10-14&hasta=2026-10-16&estado=cancelado',
                    desde='2026-10-14', hasta='2026-10-16', estado='cancelado')
    assert f'>{fx["v_canc"]}</td>' in html
    assert f'>{fx["v_dentro"]}</td>' not in html


def test_filtros_conservados_en_enlaces(fx):
    html = fx['c'].get('/pedidos?desde=2026-10-14&hasta=2026-10-16'
                       ).get_data(as_text=True)
    plano = _html.unescape(html)
    assert 'desde=2026-10-14&hasta=2026-10-16&pagina=2' in plano


def test_filtros_invalidos_avisan_y_no_rompen(fx):
    total = len(_ids_esperados(fx))
    fin = min(POR_PAGINA, total)
    html = fx['c'].get('/pedidos?desde=abc&estado=zzz-no-existe'
                       ).get_data(as_text=True)
    assert 'Fecha inválida' in html
    assert 'Estado no válido' in html
    assert f'Mostrando 1–{fin} de {total}' in html


def test_form_reportes_generar_deshabilitado(fx):
    """Opción (b): el form que apuntaba a /reportes/generar (404) queda
    deshabilitado con una nota visible."""
    html = fx['c'].get('/reportes').get_data(as_text=True)
    assert '<fieldset disabled>' in html
    assert 'en redefinición' in html
