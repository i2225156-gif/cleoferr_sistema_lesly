"""Resolver una alerta = enterada: la sincronizacion NO la recrea.

Reproduce el bug reportado: al pulsar "Resolver" la alerta queda
resuelta, pero la sincronizacion de GET /alertas la VUELVE A CREAR
(fila nueva con fecha de ahora) mientras la condicion sigue igual:
el usuario resuelve y ve que no pasa nada.

Reglas implementadas (sin cambio de esquema):
- Vencimiento (tipo 3): no se recrea mientras sea el mismo producto con
  la misma fecha de vencimiento; si se recrea si la fecha cambia o si el
  producto pasa a estar vencido.
- Stock bajo (tipo 1): no se recrea mientras el stock no baje por debajo
  del nivel que tenia al resolver, ni antes de ALERTA_SNOOZE_DIAS
  (variable de entorno, default 7).

La referencia (nivel de stock / fecha de vencimiento) se deriva de la
descripcion de la alerta, que la app escribe y refresca al resolver;
`fecha` es el ultimo cambio de estado (creacion o resolucion) y se usa
para la calma (snooze).

Productos y alertas sinteticos dentro de una transaccion con rollback.
"""
import json
import shutil
import subprocess
import uuid as _uuid
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app

HARNESS = Path(__file__).resolve().parent / 'js' / 'alertas_harness.mjs'


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


def _sesion():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
        s['nombre'] = 'Prueba'
        s['_csrf_token'] = 'token-test'
    return c


@pytest.fixture()
def fx(monkeypatch):
    """Dos productos sinteticos: uno con stock bajo (tipo 1) y otro que
    vence pronto (tipo 3). Las alertas las crea la propia sincronizacion
    de GET /alertas (no se insertan a mano: se prueba el camino real)."""
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo)
        VALUES (%s, 9.99, 3, TRUE) RETURNING id_producto
    """, (f"testenter-{_uuid.uuid4().hex[:10]}",))
    id_stock = cur.fetchone()['id_producto']
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, activo, fecha_vencimiento)
        VALUES (%s, 9.99, 50, TRUE, CURRENT_DATE + INTERVAL '5 days')
        RETURNING id_producto
    """, (f"testenter-{_uuid.uuid4().hex[:10]}",))
    id_venc = cur.fetchone()['id_producto']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    monkeypatch.setenv('VENCIMIENTO_UMBRAL_DIAS', '30')
    yield {'cur': cur, 'admin': _sesion(),
           'id_stock': id_stock, 'id_venc': id_venc}
    proxy.rollback()
    raw.close()


def _sync(fx):
    r = fx['admin'].get('/alertas')
    assert r.status_code == 200


def _alertas_de(fx, id_producto, tipo):
    fx['cur'].execute("""
        SELECT id_alerta, descripcion, resuelta, fecha
          FROM alerta
         WHERE id_producto = %s AND id_tipo_alerta = %s
         ORDER BY id_alerta
    """, (id_producto, tipo))
    return fx['cur'].fetchall()


def _sin_resolver(fx, id_producto, tipo):
    return [a for a in _alertas_de(fx, id_producto, tipo) if not a['resuelta']]


def _resolver(fx, id_alerta):
    r = fx['admin'].post(f'/alertas/resolver/{id_alerta}', headers={
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': 'token-test',
        'Accept': 'application/json',
    })
    assert r.status_code == 200
    assert r.get_json()['ok'] is True
    return r


def _poner_stock(fx, id_producto, stock):
    fx['cur'].execute("UPDATE producto SET stock = %s WHERE id_producto = %s",
                      (stock, id_producto))


def _poner_vencimiento(fx, id_producto, dias):
    fx['cur'].execute(
        "UPDATE producto SET fecha_vencimiento = CURRENT_DATE + make_interval(days => %s)"
        " WHERE id_producto = %s", (dias, id_producto))


# ── El bug reportado ───────────────────────────────────────────────────

def test_resolver_una_alerta_de_stock_no_hace_que_vuelva_a_salir(fx):
    """Resolver = enterada: tras recargar, la sincronizacion NO crea una
    alerta nueva del mismo producto (la condicion sigue igual)."""
    _sync(fx)
    pendientes = _sin_resolver(fx, fx['id_stock'], 1)
    assert len(pendientes) == 1
    _resolver(fx, pendientes[0]['id_alerta'])
    _sync(fx)
    assert _sin_resolver(fx, fx['id_stock'], 1) == []
    assert len(_alertas_de(fx, fx['id_stock'], 1)) == 1


def test_resolver_una_alerta_de_vencimiento_no_hace_que_vuelva_a_salir(fx):
    """Mismo caso con una alerta de vencimiento: misma fecha y mismo
    estado (por vencer) -> no se recrea."""
    _sync(fx)
    pendientes = _sin_resolver(fx, fx['id_venc'], 3)
    assert len(pendientes) == 1
    _resolver(fx, pendientes[0]['id_alerta'])
    _sync(fx)
    assert _sin_resolver(fx, fx['id_venc'], 3) == []
    assert len(_alertas_de(fx, fx['id_venc'], 3)) == 1


# ── Vencimiento: si la condicion cambia, vuelve a avisar ──────────────

def test_si_cambia_la_fecha_de_vencimiento_se_vuelve_a_avisar(fx):
    """La referencia cambio (otra fecha de vencimiento) -> alerta nueva."""
    _sync(fx)
    pendientes = _sin_resolver(fx, fx['id_venc'], 3)
    assert len(pendientes) == 1
    _resolver(fx, pendientes[0]['id_alerta'])
    _poner_vencimiento(fx, fx['id_venc'], 12)
    _sync(fx)
    assert len(_sin_resolver(fx, fx['id_venc'], 3)) == 1


def test_si_el_producto_pasa_a_estar_vencido_se_vuelve_a_avisar(fx):
    """Misma fecha pero el producto pasa a vencido -> alerta nueva."""
    _sync(fx)
    pendientes = _sin_resolver(fx, fx['id_venc'], 3)
    assert len(pendientes) == 1
    _resolver(fx, pendientes[0]['id_alerta'])
    _poner_vencimiento(fx, fx['id_venc'], -1)
    _sync(fx)
    nuevas = _sin_resolver(fx, fx['id_venc'], 3)
    assert len(nuevas) == 1
    assert nuevas[0]['descripcion'].startswith('Vencido el')


# ── Stock bajo: la calma (ALERTA_SNOOZE_DIAS) ─────────────────────────

def test_si_el_stock_baja_no_vuelve_a_salir_durante_la_calma(fx, monkeypatch):
    """El stock baja por debajo del nivel que tenia al resolver, pero
    todavia no han pasado los dias de calma: no se recrea."""
    monkeypatch.setenv('ALERTA_SNOOZE_DIAS', '7')
    _sync(fx)
    pendientes = _sin_resolver(fx, fx['id_stock'], 1)
    assert len(pendientes) == 1
    _resolver(fx, pendientes[0]['id_alerta'])
    _poner_stock(fx, fx['id_stock'], 1)
    _sync(fx)
    assert _sin_resolver(fx, fx['id_stock'], 1) == []
    assert len(_alertas_de(fx, fx['id_stock'], 1)) == 1


def test_si_el_stock_baja_y_paso_la_calma_se_vuelve_a_avisar(fx, monkeypatch):
    """Con ALERTA_SNOOZE_DIAS = 0 (sin calma) el stock que baja por
    debajo del nivel al resolver vuelve a generar alerta."""
    monkeypatch.setenv('ALERTA_SNOOZE_DIAS', '0')
    _sync(fx)
    pendientes = _sin_resolver(fx, fx['id_stock'], 1)
    assert len(pendientes) == 1
    _resolver(fx, pendientes[0]['id_alerta'])
    _poner_stock(fx, fx['id_stock'], 1)
    _sync(fx)
    nuevas = _sin_resolver(fx, fx['id_stock'], 1)
    assert len(nuevas) == 1
    assert 'tiene 1 unidades' in nuevas[0]['descripcion']


def test_la_calma_por_defecto_es_de_siete_dias(fx, monkeypatch):
    """ALERTA_SNOOZE_DIAS no definido -> 7 dias de calma."""
    monkeypatch.delenv('ALERTA_SNOOZE_DIAS', raising=False)
    from services.kpis import alerta_snooze_dias
    assert alerta_snooze_dias() == 7


# ── El AJAX: quitar la fila, actualizar contadores y avisar ───────────

def test_el_ajax_quita_la_fila_actualiza_los_contadores_y_avisa(fx):
    """En el navegador la fila desaparece con una animacion corta, los
    contadores bajan en el sitio (sin recargar) y sale el toast."""
    html = fx['admin'].get('/alertas').get_data(as_text=True)
    if shutil.which('node') is None:
        pytest.skip('node no disponible')
    proc = subprocess.run(
        ['node', str(HARNESS)],
        input=json.dumps({'html': html, 'idAlerta': 4242}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    salida = json.loads(proc.stdout)
    assert salida['errores'] == []
    assert salida['filaEliminada'] is True
    assert salida['animacion'] is True
    assert salida['contador'] == '1'
    assert salida['avisos'] == [
        {'mensaje': 'Alerta marcada como resuelta.', 'tipo': 'ok'}]
