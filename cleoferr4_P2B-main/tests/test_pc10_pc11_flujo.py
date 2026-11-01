"""Pruebas del flujo PC10→PC11 con transacción local y rollback."""
from pathlib import Path
import uuid as _uuid

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


class ConnProxy:
    """Evita commits reales; el fixture revierte todo al terminar."""

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
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute("""SELECT id_producto FROM producto
                    WHERE activo = TRUE AND stock >= 1
                    ORDER BY id_producto LIMIT 1""")
    prod = cur.fetchone()
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    client = app.test_client()
    with client.session_transaction() as s:
        s['usuario_id'] = 11
        s['rol'] = 'cliente'
        s['nombre'] = 'Cliente'
        s['_csrf_token'] = 'token-test'
    yield {'client': client, 'cur': cur, 'proxy': proxy,
           'id_producto': prod['id_producto']}
    proxy.rollback()
    raw.close()


def _crear_pedido(fx, metodo='tarjeta'):
    r = fx['client'].post('/carrito/confirmar_v2', json={
        'items': [{'id_producto': fx['id_producto'], 'cantidad': 1}],
        'metodo_pago': metodo,
        'tipo_comprobante': 'boleta_simple',
        'csrf_token': 'token-test',
    })
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['ok'] is True
    return cuerpo


def _pago_de(fx, id_transaccion):
    fx['cur'].execute("""
        SELECT p.id_pago, p.id_venta, p.estado AS estado_pago,
               tp.nombre AS tipo_pago,
               p.detalle_proveedor->>'metodo' AS metodo_final,
               v.num_operacion, ev.nombre AS estado_venta
          FROM pago p
          JOIN venta v ON v.id_venta = p.id_venta
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
          LEFT JOIN tipo_pago tp ON tp.id_tipo_pago = p.id_tipo_pago
         WHERE p.id_transaccion = %s
    """, (id_transaccion,))
    return fx['cur'].fetchone()


@pytest.mark.parametrize('metodo_final,tipo_esperado', [
    ('yape', 'yape_plin'),
    ('plin', 'yape_plin'),
    ('transferencia', 'efectivo'),
])
def test_cambio_de_metodo_guarda_el_metodo_final_y_el_mismo_pedido(
        fx, metodo_final, tipo_esperado):
    pedido = _crear_pedido(fx, metodo='tarjeta')
    trx = pedido['checkout_url'].rstrip('/').rsplit('/', 1)[-1]
    pago_inicial = _pago_de(fx, trx)
    assert pago_inicial['tipo_pago'] == 'tarjeta'
    fx['cur'].execute("SELECT COUNT(*) AS n FROM venta")
    ventas_antes = fx['cur'].fetchone()['n']

    datos = {'metodo_pago': metodo_final, 'csrf_token': 'token-test'}
    if metodo_final in ('yape', 'plin'):
        datos['codigo_operacion'] = '654321'
    else:
        datos['numero_operacion'] = 'OP-' + _uuid.uuid4().hex[:12]
    r = fx['client'].post(f'/pago/simulado/{trx}/procesar', json=datos)
    assert r.status_code == 200
    assert r.get_json()['id_pedido'] == pedido['id_pedido']

    pago_final = _pago_de(fx, trx)
    assert pago_final['id_pago'] == pago_inicial['id_pago']
    assert pago_final['id_venta'] == pago_inicial['id_venta']
    assert pago_final['metodo_final'] == metodo_final
    assert pago_final['tipo_pago'] == tipo_esperado
    assert pago_final['estado_pago'] == 'pendiente'
    assert pago_final['estado_venta'] == 'pendiente'

    with fx['client'].session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Administradora'
        s['_csrf_token'] = 'token-test'
    r = fx['client'].post(
        f"/admin/pagos_simulados/pago/{pago_final['id_pago']}/confirmar",
        data={'csrf_token': 'token-test'})
    assert r.status_code == 302
    pago_aprobado = _pago_de(fx, trx)
    assert pago_aprobado['estado_pago'] == 'confirmado'
    assert pago_aprobado['estado_venta'] == 'confirmado'
    assert pago_aprobado['tipo_pago'] == tipo_esperado
    assert pago_aprobado['metodo_final'] == metodo_final
    fx['cur'].execute("SELECT COUNT(*) AS n FROM venta")
    assert fx['cur'].fetchone()['n'] == ventas_antes


@pytest.mark.parametrize('metodo_final', ['yape', 'plin'])
def test_operacion_yape_plin_se_guarda_en_la_misma_venta(fx, metodo_final):
    pedido = _crear_pedido(fx, metodo='tarjeta')
    trx = pedido['checkout_url'].rstrip('/').rsplit('/', 1)[-1]
    assert _pago_de(fx, trx)['num_operacion'] is None
    codigo = str(100000 + (_uuid.uuid4().int % 900000))

    r = fx['client'].post(f'/pago/simulado/{trx}/procesar', json={
        'metodo_pago': metodo_final,
        'codigo_operacion': codigo,
        'csrf_token': 'token-test',
    })

    assert r.status_code == 200
    pago = _pago_de(fx, trx)
    assert pago['id_venta'] == pedido['id_pedido']
    assert pago['metodo_final'] == metodo_final
    assert pago['num_operacion'] == codigo
    assert pago['estado_venta'] == 'pendiente'


def test_contrato_http_de_compra_y_pago_no_acepta_datos_de_tarjeta():
    raiz = Path(__file__).resolve().parent.parent
    carrito = (raiz / 'templates' / 'carrito.html').read_text(encoding='utf-8')
    pago_html = (raiz / 'templates' / 'pago_simulado.html').read_text(encoding='utf-8')
    app_py = (raiz / 'app.py').read_text(encoding='utf-8')
    confirmacion = carrito.split('function confirmarPedidoFinal()', 1)[1]
    envio_pago = pago_html.split(
        "document.getElementById('formPagoSimulado').addEventListener('submit'", 1)[1]
    procesamiento = app_py.split('def pago_simulado_procesar', 1)[1].split(
        '@app.route', 1)[0]

    for bloque in (confirmacion, envio_pago, procesamiento):
        for campo in ('numero_tarjeta', 'vencimiento', 'cvv'):
            assert f'payload.{campo}' not in bloque
            assert f"data.get('{campo}'" not in bloque
            assert f'{campo}:' not in bloque


def test_tarjeta_aprobada_sin_enviar_numero_vencimiento_ni_cvv(fx):
    pedido = _crear_pedido(fx, metodo='tarjeta')
    trx = pedido['checkout_url'].rstrip('/').rsplit('/', 1)[-1]

    r = fx['client'].post(f'/pago/simulado/{trx}/procesar', json={
        'metodo_pago': 'tarjeta',
        'resultado': 'aprobado',
        'csrf_token': 'token-test',
    })

    assert r.status_code == 200
    assert r.get_json()['estado'] == 'confirmado'
    pago = _pago_de(fx, trx)
    assert pago['estado_pago'] == 'confirmado'
    assert pago['estado_venta'] == 'confirmado'


def test_metodo_se_conserva_en_recarga_otra_pestana_y_regreso(fx):
    pedido = _crear_pedido(fx, metodo='yape')
    url = pedido['checkout_url']
    respuestas = [fx['client'].get(url), fx['client'].get(url)]
    fx['client'].get('/carrito')
    respuestas.append(fx['client'].get(url))

    otra_pestana = app.test_client()
    with otra_pestana.session_transaction() as s:
        s['usuario_id'] = 11
        s['rol'] = 'cliente'
        s['nombre'] = 'Cliente'
        s['_csrf_token'] = 'token-test'
    respuestas.append(otra_pestana.get(url))

    for r in respuestas:
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert 'const METODO_INICIAL = "yape";' in html
        assert 'sessionStorage' not in html


def test_boton_se_bloquea_y_doble_confirmacion_reutiliza_el_pedido(fx):
    html = fx['client'].get('/carrito').get_data(as_text=True)
    assert 'btnConfirmar.disabled = true' in html
    primero = _crear_pedido(fx)
    fx['cur'].execute("SELECT COUNT(*) AS n FROM venta")
    ventas_antes = fx['cur'].fetchone()['n']

    segundo = _crear_pedido(fx)

    assert segundo['reutilizado'] is True
    assert segundo['id_pedido'] == primero['id_pedido']
    assert segundo['id_pago'] == primero['id_pago']
    assert segundo['checkout_url'] == primero['checkout_url']
    fx['cur'].execute("SELECT COUNT(*) AS n FROM venta")
    assert fx['cur'].fetchone()['n'] == ventas_antes
