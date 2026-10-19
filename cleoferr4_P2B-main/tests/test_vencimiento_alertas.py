"""TAREA 3.2 — alertas de vencido y por vencer (tipo 3 vencimiento_proximo).

El sync de /alertas ahora cubre RF17: vencidos (fecha < hoy, prioridad
alta) y por vencer (dentro de VENCIMIENTO_UMBRAL_DIAS, prioridad media).
Una alerta sin resolver por producto; si el estado empeora, la existente
se actualiza (no se duplica); las stale se resuelven. Todo dentro de la
transacción con rollback del fixture.
"""
import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app


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
    monkeypatch.setenv('VENCIMIENTO_UMBRAL_DIAS', '30')
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    # Cuatro productos sintéticos: vencido, por vencer, lejano y sin fecha.
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, fecha_vencimiento)
        VALUES ('zzqval-vencido', 5.00, 4, CURRENT_DATE - 5),
               ('zzqval-porvencer', 5.00, 6, CURRENT_DATE + 10),
               ('zzqval-lejano', 5.00, 7, CURRENT_DATE + 400),
               ('zzqval-sinfecha', 5.00, 8, NULL)
        RETURNING id_producto, nombre
    """)
    productos = {r['nombre']: r['id_producto'] for r in cur.fetchall()}
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'c': _vendedor(), 'cur': cur, 'p': productos}
    proxy.rollback()
    raw.close()


def _alertas_tipo3(fx, solo_activas=True):
    sql = ("SELECT id_alerta, id_producto, descripcion, prioridad, resuelta"
           " FROM alerta WHERE id_tipo_alerta = 3 AND id_producto = ANY(%s)")
    params = [list(fx['p'].values())]
    if solo_activas:
        sql += " AND resuelta = FALSE"
    fx['cur'].execute(sql + " ORDER BY id_alerta", params)
    return fx['cur'].fetchall()


def _sync(fx):
    r = fx['c'].get('/alertas')
    assert r.status_code == 200


def test_crea_alertas_de_vencido_y_por_vencer(fx):
    _sync(fx)
    filas = _alertas_tipo3(fx)
    assert len(filas) == 2
    por_producto = {f['id_producto']: f for f in filas}
    vencido = por_producto[fx['p']['zzqval-vencido']]
    porvencer = por_producto[fx['p']['zzqval-porvencer']]
    assert vencido['descripcion'].startswith('Vencido el')
    assert vencido['prioridad'] == 'alta'
    assert porvencer['descripcion'].startswith('Vence el')
    assert porvencer['prioridad'] == 'media'
    # el lejano y el sin fecha NO generan alerta
    ids = {f['id_producto'] for f in filas}
    assert fx['p']['zzqval-lejano'] not in ids
    assert fx['p']['zzqval-sinfecha'] not in ids


def test_no_duplica_en_segunda_sincronizacion(fx):
    _sync(fx)
    _sync(fx)
    assert len(_alertas_tipo3(fx)) == 2


def test_empeorar_actualiza_la_alerta_existente(fx):
    _sync(fx)
    antes = _alertas_tipo3(fx)
    porvencer = [f for f in antes if f['id_producto'] == fx['p']['zzqval-porvencer']][0]
    assert porvencer['descripcion'].startswith('Vence el')
    # el producto vence (fecha al pasado): la MISMA alerta pasa a vencido
    fx['cur'].execute("UPDATE producto SET fecha_vencimiento = CURRENT_DATE - 1"
                      " WHERE id_producto = %s", (fx['p']['zzqval-porvencer'],))
    _sync(fx)
    despues = {f['id_producto']: f for f in _alertas_tipo3(fx)}
    actualizada = despues[fx['p']['zzqval-porvencer']]
    assert actualizada['id_alerta'] == porvencer['id_alerta']  # no duplicó
    assert actualizada['descripcion'].startswith('Vencido el')
    assert actualizada['prioridad'] == 'alta'


def test_stale_se_resuelve_al_quitar_la_fecha(fx):
    _sync(fx)
    assert len(_alertas_tipo3(fx)) == 2
    fx['cur'].execute("UPDATE producto SET fecha_vencimiento = NULL"
                      " WHERE id_producto = %s", (fx['p']['zzqval-vencido'],))
    _sync(fx)
    activas = {f['id_producto'] for f in _alertas_tipo3(fx)}
    assert fx['p']['zzqval-vencido'] not in activas
    # y la resuelta queda registrada como tal
    todas = _alertas_tipo3(fx, solo_activas=False)
    resueltas = [f for f in todas
                 if f['id_producto'] == fx['p']['zzqval-vencido']]
    assert any(f['resuelta'] for f in resueltas)


def test_umbral_configurable(fx, monkeypatch):
    monkeypatch.setenv('VENCIMIENTO_UMBRAL_DIAS', '3')
    _sync(fx)
    # con umbral 3, el producto que vence en 10 días ya no entra
    activas = {f['id_producto'] for f in _alertas_tipo3(fx)}
    assert fx['p']['zzqval-vencido'] in activas
    assert fx['p']['zzqval-porvencer'] not in activas
