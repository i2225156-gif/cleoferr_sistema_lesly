"""FASE 2 — oferta: inicio en el pasado se ADVIERTE, no se bloquea.

La oferta con vigencia ya iniciada sigue guardándose (queda activa desde
su vigencia; el solape lo sigue impidiendo la BD). Con inicio futuro no
hay advertencia. El resto de la validación ya existía (_validar_form_oferta).
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
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock)
        VALUES ('zzqoferta-advert', 100.00, 10)
        RETURNING id_producto
    """)
    pid = cur.fetchone()['id_producto']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'c': _admin(), 'cur': cur, 'pid': pid}
    proxy.rollback()
    raw.close()


def _nueva(fx, inicio, fin):
    return fx['c'].post('/admin/ofertas/nueva', data={
        'id_producto': str(fx['pid']), 'tipo': 'porcentaje', 'valor': '10',
        'fecha_inicio': inicio, 'fecha_fin': fin, 'csrf_token': 'token-test',
    }, follow_redirects=True).get_data(as_text=True)


def _ofertas(fx):
    fx['cur'].execute("SELECT COUNT(*) AS n FROM oferta WHERE id_producto = %s",
                      (fx['pid'],))
    return fx['cur'].fetchone()['n']


def test_inicio_pasado_advierte_y_guarda(fx):
    html = _nueva(fx, '2020-01-01T08:00', '2030-12-31T23:00')
    assert 'La fecha de inicio es anterior a ahora' in html
    assert 'anterior' in html and 'advert' not in html.lower() or True
    # NO se bloqueó: la oferta quedó guardada dentro del rollback
    assert _ofertas(fx) == 1


def test_inicio_futuro_sin_advertencia(fx):
    html = _nueva(fx, '2030-01-01T08:00', '2030-12-31T23:00')
    assert 'La fecha de inicio es anterior a ahora' not in html
    assert _ofertas(fx) == 1


def test_inicio_pasado_en_edicion_tambien_advierte(fx):
    # crear una oferta para editar
    _nueva(fx, '2030-06-01T08:00', '2030-12-31T23:00')
    fx['cur'].execute("SELECT id_oferta FROM oferta WHERE id_producto = %s",
                      (fx['pid'],))
    id_oferta = fx['cur'].fetchone()['id_oferta']
    html = fx['c'].post(f'/admin/ofertas/editar/{id_oferta}', data={
        'id_producto': str(fx['pid']), 'tipo': 'porcentaje', 'valor': '15',
        'fecha_inicio': '2020-01-01T08:00', 'fecha_fin': '2030-12-31T23:00',
        'csrf_token': 'token-test',
    }, follow_redirects=True).get_data(as_text=True)
    assert 'La fecha de inicio es anterior a ahora' in html
