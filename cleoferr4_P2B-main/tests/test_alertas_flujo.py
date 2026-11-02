"""Flujo de /alertas: orden por urgencia, filtros rápidos y acciones.

Sin tocar la sincronización ni el esquema: se montan productos y alertas
SINTÉTICOS (dentro de una transacción con rollback, nada persiste) y se
comprueba lo que ve el usuario:

  * el listado ordena por urgencia (vencido, por vencer de menor a mayor
    plazo, stock bajo de menor a mayor unidades);
  * el filtro rápido (Todas / Stock bajo / Vencimiento) llega por GET con
    lista blanca: un valor inventado NO da 500 (se ve «todas»);
  * el contador de la cabecera refleja el filtro aplicado;
  * cada alerta ofrece la acción de SU tipo con el id real del producto
    («Reponer» -> /inventario, «Crear oferta» -> /admin/ofertas, «Ver
    producto» -> /productos/detalle/<id>);
  * una acción solo se muestra si el usuario tiene el permiso del módulo
    de destino (inventario / ofertas / productos);
  * «Marcar como atendida» sustituye a «Resolver» (el AJAX sigue igual:
    la fila se desvanece y el contador baja).

Los permisos del vendedor de prueba se inyectan con un doble de la
conexión AUTH (patrón de tests/test_admin_layout.py).
"""
import re
import uuid as _uuid
from urllib.parse import quote

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


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


class ConnPermisos:
    """Doble de la conexión AUTH: la consulta de permisos del
    before_request se sirve con filas inyectadas (determinista)."""

    def __init__(self, claves):
        self.claves = list(claves)
        self.filas = []
        self.cur_real = None

    def cursor(self, dictionary=False, **kwargs):
        self.cur_real = None
        return self

    def execute(self, sql, params=None):
        if 'usuario_permiso' in sql:
            if self.claves:
                self.filas = [{'activo': True, 'permiso': c,
                               'plantilla_sembrada': True}
                              for c in self.claves]
            else:
                self.filas = [{'activo': True, 'permiso': None,
                               'plantilla_sembrada': True}]
        else:
            self.filas = []

    def fetchall(self):
        return self.filas

    def fetchone(self):
        return self.filas[0] if self.filas else None

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


PERMISOS_TODOS = ('ventas', 'pedidos', 'clientes', 'alertas', 'productos',
                  'ofertas', 'proveedores', 'inventario', 'reportes')


def _sesion(rol, uid=8):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    """Cinco productos sintéticos y las alertas que crea la sincronización:

      * stock2  (2 unidades)  y stock7 (7 unidades) -> tipo 1 (stock bajo)
      * vencido (hace 3 días)                      -> tipo 3 (vencido)
      * porvencer2 (en 2 días) y porvencer10 (en 10 días) -> tipo 3

    Los de vencimiento van con stock 50 (fuera del umbral de stock) para
    que NO generen además una alerta de stock.
    """
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    marca = _uuid.uuid4().hex[:8]
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo) VALUES
            (%s, 9.99, 2, TRUE),
            (%s, 9.99, 7, TRUE)
        RETURNING id_producto, nombre
    """, (f'zzfl-stock2-{marca}', f'zzfl-stock7-{marca}'))
    prods = {r['nombre'].split('-')[1]: r['id_producto'] for r in cur.fetchall()}
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo,
                              fecha_vencimiento) VALUES
            (%s, 9.99, 50, TRUE, CURRENT_DATE - 3),
            (%s, 9.99, 50, TRUE, CURRENT_DATE + 2),
            (%s, 9.99, 50, TRUE, CURRENT_DATE + 10)
        RETURNING id_producto, nombre
    """, (f'zzfl-vencido-{marca}', f'zzfl-porvencer2-{marca}',
          f'zzfl-porvencer10-{marca}'))
    for r in cur.fetchall():
        prods[r['nombre'].split('-')[1]] = r['id_producto']

    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    monkeypatch.setattr(modulo_app, 'get_connection_auth',
                        lambda: ConnPermisos(PERMISOS_TODOS))
    cliente = _sesion('vendedor')
    # La sincronización (GET /alertas) crea las alertas de los productos
    # sintéticos: es el camino real, no una inserción a mano.
    r = cliente.get('/alertas')
    assert r.status_code == 200
    alertas = {}
    for clave, id_producto in prods.items():
        cur.execute("""
            SELECT id_alerta FROM alerta
             WHERE id_producto = %s AND resuelta = FALSE
             ORDER BY id_alerta LIMIT 1
        """, (id_producto,))
        fila = cur.fetchone()
        assert fila, f'no se creo la alerta de {clave}'
        alertas[clave] = fila['id_alerta']
    yield {'cur': cur, 'proxy': proxy, 'cliente': cliente,
           'prods': prods, 'alertas': alertas}
    proxy.rollback()
    raw.close()


# ── Utilidades de lectura del HTML ────────────────────────────────

def _html(cliente, url='/alertas'):
    r = cliente.get(url)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _ids_fila(html):
    """ids de alerta en el orden en que salen en la tabla."""
    return re.findall(r'<tr data-id="(\d+)">', html)


def _fila(html, id_alerta):
    m = re.search(r'<tr data-id="%s">(.*?)</tr>' % id_alerta, html, re.DOTALL)
    assert m, f'la alerta {id_alerta} no aparece en el listado'
    return m.group(1)


def _contador(html):
    m = re.search(r'id="contador-alertas"[^>]*>(\d+)<', html)
    assert m, 'no se encuentra el contador de alertas'
    return int(m.group(1))


# ── 1. Orden por urgencia ─────────────────────────────────────────

def test_el_listado_ordena_por_urgencia(fx):
    """Vencidos primero, luego por vencer (menos días primero) y stock
    bajo (menos unidades primero)."""
    html = _html(fx['cliente'])
    ids = _ids_fila(html)
    a = fx['alertas']
    for clave in ('vencido', 'porvencer2', 'porvencer10', 'stock2', 'stock7'):
        assert str(a[clave]) in ids, f'falta la alerta de {clave}'
    # vencido < por vencer (2 días) < por vencer (10 días) < stock (2) < stock (7)
    assert ids.index(str(a['vencido'])) < ids.index(str(a['porvencer2']))
    assert ids.index(str(a['porvencer2'])) < ids.index(str(a['porvencer10']))
    assert ids.index(str(a['porvencer10'])) < ids.index(str(a['stock2']))
    assert ids.index(str(a['stock2'])) < ids.index(str(a['stock7']))


# ── 2. Filtros rápidos ────────────────────────────────────────────

def test_el_filtro_stock_bajo_muestra_solo_alertas_de_stock(fx):
    html = _html(fx['cliente'], '/alertas?tipo=stock_bajo')
    ids = _ids_fila(html)
    assert str(fx['alertas']['stock2']) in ids
    assert str(fx['alertas']['stock7']) in ids
    assert str(fx['alertas']['vencido']) not in ids
    assert str(fx['alertas']['porvencer2']) not in ids


def test_el_filtro_vencimiento_muestra_solo_alertas_de_vencimiento(fx):
    html = _html(fx['cliente'], '/alertas?tipo=vencimiento')
    ids = _ids_fila(html)
    assert str(fx['alertas']['vencido']) in ids
    assert str(fx['alertas']['porvencer2']) in ids
    assert str(fx['alertas']['stock2']) not in ids
    assert str(fx['alertas']['stock7']) not in ids


def test_el_contador_de_la_cabecera_refleja_el_filtro(fx):
    """El contador muestra cuántas alertas quedan con el filtro puesto."""
    cur = fx['cur']
    cur.execute("""SELECT COUNT(*) AS n FROM alerta
                    WHERE resuelta = FALSE AND id_tipo_alerta IN (1, 2)""")
    esperado_stock = cur.fetchone()['n']
    cur.execute("""SELECT COUNT(*) AS n FROM alerta
                    WHERE resuelta = FALSE AND id_tipo_alerta = 3""")
    esperado_venc = cur.fetchone()['n']
    cur.execute("""SELECT COUNT(*) AS n FROM alerta WHERE resuelta = FALSE""")
    esperado_todas = cur.fetchone()['n']

    assert _contador(_html(fx['cliente'], '/alertas?tipo=stock_bajo')) \
        == esperado_stock
    assert _contador(_html(fx['cliente'], '/alertas?tipo=vencimiento')) \
        == esperado_venc
    assert _contador(_html(fx['cliente'])) == esperado_todas


def test_un_filtro_invalido_no_da_500_y_muestra_todas(fx):
    """El parámetro GET se valida con lista blanca: un valor inventado
    se ignora (se ve «todas»), nunca una página de error."""
    html_todas = _html(fx['cliente'], '/alertas?tipo=todas')
    for basura in ('inventado', "';DROP_TABLE_alerta;--", '<b>x</b>', '',
                   'stock', 'vencimientos'):
        r = fx['cliente'].get('/alertas?tipo=%s' % quote(basura, safe=''))
        assert r.status_code == 200
        assert _ids_fila(r.get_data(as_text=True)) == _ids_fila(html_todas)


def test_el_filtro_acepta_solo_los_valores_de_la_lista_blanca(fx):
    """Mayúsculas y espacios sobrantes se normalizan (el filtro es el
    mismo), pero un valor que no está en la lista se ignora."""
    html_stock = _html(fx['cliente'], '/alertas?tipo=stock_bajo')
    html_normalizado = _html(fx['cliente'], '/alertas?tipo=STOCK_BAJO')
    assert _ids_fila(html_normalizado) == _ids_fila(html_stock)
    assert str(fx['alertas']['stock2']) in _ids_fila(html_normalizado)
    assert str(fx['alertas']['vencido']) not in _ids_fila(html_normalizado)


# ── 3. Acciones por alerta ────────────────────────────────────────

def test_la_alerta_de_stock_ofrece_reponer_con_el_id_del_producto(fx):
    """Stock bajo -> «Reponer» al formulario de ENTRADA de /inventario con
    el producto preseleccionado (id real)."""
    fila = _fila(_html(fx['cliente']), fx['alertas']['stock2'])
    assert 'Reponer' in fila
    assert f'href="/inventario?producto={fx["prods"]["stock2"]}"' in fila


def test_la_alerta_de_vencimiento_ofrece_crear_oferta_y_ver_producto(fx):
    """Vencimiento -> «Crear oferta» (a /admin/ofertas) y «Ver producto»
    (a /productos/detalle/<id real>)."""
    fila = _fila(_html(fx['cliente']), fx['alertas']['vencido'])
    assert 'Crear oferta' in fila
    assert 'href="/admin/ofertas"' in fila
    assert 'Ver producto' in fila
    assert f'href="/productos/detalle/{fx["prods"]["vencido"]}"' in fila


def test_la_alerta_de_stock_no_ofrece_crear_oferta(fx):
    """«Crear oferta» es solo de las alertas de vencimiento."""
    fila = _fila(_html(fx['cliente']), fx['alertas']['stock2'])
    assert 'Crear oferta' not in fila


def test_sin_permiso_del_modulo_no_se_muestra_la_accion(monkeypatch, fx):
    """Cada acción solo se muestra con el permiso de SU módulo destino:
    un vendedor con solo «alertas» no ve Reponer, Crear oferta ni Ver
    producto (y sus enlaces no apuntan a rutas prohibidas)."""
    monkeypatch.setattr(modulo_app, 'get_connection_auth',
                        lambda: ConnPermisos(('alertas',)))
    html = _html(fx['cliente'])
    fila_stock = _fila(html, fx['alertas']['stock2'])
    fila_venc = _fila(html, fx['alertas']['vencido'])
    for fila in (fila_stock, fila_venc):
        assert 'Reponer' not in fila
        assert 'Crear oferta' not in fila
        assert 'Ver producto' not in fila
        assert '/inventario?producto=' not in fila
        assert '/productos/detalle/' not in fila


def test_con_permiso_del_modulo_si_se_muestra_la_accion(fx):
    """Con los permisos de los módulos destino, las acciones aparecen."""
    html = _html(fx['cliente'])
    fila_stock = _fila(html, fx['alertas']['stock2'])
    assert f'href="/inventario?producto={fx["prods"]["stock2"]}"' in fila_stock
    fila_venc = _fila(html, fx['alertas']['vencido'])
    assert 'href="/admin/ofertas"' in fila_venc
    assert f'href="/productos/detalle/{fx["prods"]["vencido"]}"' in fila_venc


def test_el_inventario_preselecciona_el_producto_recibido(fx):
    """/inventario?producto=<id> deja el producto elegido en el formulario
    de entrada (y un parámetro inválido no rompe nada)."""
    html = _html(fx['cliente'],
                 f'/inventario?producto={fx["prods"]["stock2"]}')
    m = re.search(r'<option value="%d"[^>]*selected[^>]*>' % fx['prods']['stock2'],
                  html)
    assert m, 'el producto no queda preseleccionado en /inventario'
    assert re.search(r'<option value="entrada"[^>]*selected', html), \
        'el tipo por defecto debe ser ENTRADA (reposición)'
    # Parámetro inválido: la página carga igual, sin producto preseleccionado
    html_malo = _html(fx['cliente'], '/inventario?producto=abc')
    assert 'name="id_producto"' in html_malo


# ── 4. «Marcar como atendida» ─────────────────────────────────────

def test_el_boton_dice_marcar_como_atendida(fx):
    """«Resolver» pasa a «Marcar como atendida» con el aviso de que no
    volverá a avisar mientras la situación no cambie."""
    html = _html(fx['cliente'])
    assert 'Marcar como atendida' in html
    assert 'No volverá a avisar mientras la situación no cambie' in html
    # el texto viejo de la acción ya no está
    assert '>Resolver<' not in html
    # el AJAX sigue vivo (la fila se desvanece y el contador baja)
    assert 'resolverAlertaAjax' in html
    assert 'contador-alertas' in html
