"""JOIN tautológico en _datos_comprobante (y su regresión general).

La consulta de la venta del comprobante tenía
'LEFT JOIN estado_venta ev ON v.id_estado_venta  = v.id_estado_venta'
(ambos lados el mismo alias): fan-out x6 — una fila por cada estado_venta,
todas idénticas salvo el estado arbitrario — y fetchone() se quedaba con
la primera (la de menor id físico, 'pendiente').

- Funcional (nacido en rojo): _datos_comprobante debe devolver el estado
  REAL de la venta; con la tautología devolvía 'pendiente' para una venta
  'entregado'.
- Consulta: la réplica de la consulta de la ruta devuelve exactamente
  1 fila para una venta.
- Regresión: ninguna condición de JOIN de app.py puede ser tautológica
  (patrón ON x.col = x.col con el mismo alias a ambos lados).
"""
import re
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import _datos_comprobante

_APP_PY = Path(__file__).resolve().parent.parent / 'app.py'


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


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    # Venta sintética en estado 'entregado': con la tautología, el seq
    # scan de estado_venta casaba con las 6 filas y fetchone() devolvía
    # la primera ('pendiente', id físico 1), no el estado real.
    cur.execute("""
        INSERT INTO venta (id_tipo_venta, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='entregado'),
                'testjoin-' || md5(random()::text))
        RETURNING id_venta
    """)
    id_venta = cur.fetchone()['id_venta']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'cur': cur, 'id_venta': id_venta}
    proxy.rollback()
    raw.close()


def test_datos_comprobante_devuelve_el_estado_real(fx):
    resultado = _datos_comprobante(fx['id_venta'])
    assert resultado is not None
    venta = resultado['venta']
    assert venta['estado_venta'] == 'entregado'
    assert venta['tipo_venta'] == 'online'


def test_consulta_de_la_venta_del_comprobante_una_sola_fila(fx):
    cur = fx['cur']
    cur.execute("""
        SELECT v.*, tv.nombre AS tipo_venta, ev.nombre AS estado_venta
          FROM venta v
          LEFT JOIN tipo_venta tv   ON v.id_tipo_venta   = tv.id_tipo_venta
          LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
         WHERE v.id_venta = %s
    """, (fx['id_venta'],))
    filas = cur.fetchall()
    assert len(filas) == 1
    assert filas[0]['estado_venta'] == 'entregado'


def test_cero_joins_tautologicos_en_app_py():
    """Regresión: ninguna condición de JOIN de app.py compara una columna
    consigo misma con el mismo alias a ambos lados — el patrón exacto de
    los dos typos ya cazados (mis_pedidos y _datos_comprobante). Atrapa
    el error en el momento de escribirlo, sea cual sea la tabla."""
    fuente = _APP_PY.read_text(encoding='utf-8')
    tautologicos = re.findall(r'ON\s+(\w+)\.(\w+)\s*=\s*\1\.\2\b', fuente)
    assert not tautologicos, f'JOINs tautológicos en app.py: {tautologicos}'
