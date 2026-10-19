"""TAREA 3.3 — columna «Vence» en /productos y reporte de vencimiento.

- La tabla de /productos muestra por fila: «Vencido» (rojo), «En N d»
  (ámbar dentro del umbral) o texto tenue para fechas lejanas; — para
  productos sin fecha.
- /reportes gana la tarjeta «Vencimiento de stock»: activos con fecha
  dentro del umbral (vencidos primero), valor a costo si existe y a
  precio si no (COALESCE explícito, nunca inventado).
- De paso se corrige un bug de display latente: la columna Estado usaba
  p.estado (no existe en el SELECT) y siempre pintaba la clase
  «inactivo» con texto vacío; ahora usa p.activo.
"""
from datetime import date as _date

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
    cur.execute("""
        INSERT INTO producto (nombre, precio, stock, fecha_vencimiento, precio_costo, activo)
        VALUES ('zzqrep-vencido', 5.00, 4, CURRENT_DATE - 5, 2.00, TRUE),
               ('zzqrep-porvencer', 8.00, 6, CURRENT_DATE + 10, NULL, TRUE),
               ('zzqrep-lejano', 5.00, 7, CURRENT_DATE + 400, NULL, TRUE),
               ('zzqrep-sinfecha', 5.00, 8, NULL, NULL, TRUE),
               ('zzqrep-inactivo', 5.00, 9, NULL, NULL, FALSE)
        RETURNING nombre
    """)
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'c': _vendedor(), 'cur': cur}
    proxy.rollback()
    raw.close()


# ── columna «Vence» en /productos ────────────────────────────────

def test_columna_vence_en_productos(fx):
    # Los sintéticos (ids mayores) caen en la ÚLTIMA página por id ASC.
    html = fx['c'].get('/productos?pagina=99999').get_data(as_text=True)
    assert '<th>Vence</th>' in html
    assert '>Vencido</span>' in html                    # el de hace 5 días
    assert 'In 10 d' in html or 'En 10 d' in html
    assert 'en 400 d' in html                           # lejano: texto tenue


def test_estado_dejar_de_estar_vacio(fx):
    """Bug de display latente: p.estado no existe en el SELECT y la
    columna Estado quedaba vacía; ahora usa p.activo (garantizado por
    los sintéticos activo e inactivo del fixture, en la última página)."""
    import re
    html = fx['c'].get('/productos?pagina=99999').get_data(as_text=True)
    compacto = re.sub(r'\s+', '', html)
    assert '>activo</span>' in compacto
    assert '>inactivo</span>' in compacto


# ── reporte en /reportes ─────────────────────────────────────────

def test_reporte_de_vencimiento(fx):
    html = fx['c'].get('/reportes?pagina=1').get_data(as_text=True)
    assert 'Vencimiento de stock (30 días)' in html
    assert 'zzqrep-vencido' in html
    assert 'zzqrep-porvencer' in html
    assert 'zzqrep-lejano' not in html       # fuera del umbral
    assert 'Por vencer' in html and '>Vencido</span>' in html
    # valores: vencido con costo -> 4 x 2.00 = 8.00; por vencer sin
    # costo -> 6 x 8.00 = 48.00 (a precio, coalesce explicito)
    assert '8.00' in html and '48.00' in html


def test_reporte_orden_vencidos_primero(fx):
    html = fx['c'].get('/reportes').get_data(as_text=True)
    assert html.index('zzqrep-vencido') < html.index('zzqrep-porvencer')


def test_reporte_estado_vacio_cuando_no_hay(fx):
    fx['cur'].execute("""
        UPDATE producto SET fecha_vencimiento = CURRENT_DATE + 400
         WHERE nombre LIKE 'zzqrep-%'
    """)
    html = fx['c'].get('/reportes').get_data(as_text=True)
    assert 'Ningún producto activo vence' in html
