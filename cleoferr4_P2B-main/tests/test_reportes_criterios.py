"""Fixes de reportes: DATE_SUB -> PostgreSQL y ventas_hoy solo con pago confirmado.

Contra la BD real pero SOLO LECTURA (SELECT). La sección "Resultados" de
reportes.html:82-84 solo se renderiza si la consulta de 30 días no lanzó
excepción: antes del fix, DATE_SUB (MySQL) la vaciaba siempre.
"""
import db_supabase
from sqlalchemy import text

import app as modulo_app
from app import SQL_MOVIMIENTOS_30DIAS, SQL_VENTAS_HOY_PAGADAS, app
from services.ofertas import SQL_IMPORTE_LINEA


class ConnPermisos:
    """Doble de la conexión AUTH (patrón de tests/test_permisos.py).

    La consulta de permisos del before_request se sirve con filas
    inyectadas; cualquier otra consulta de esa BD se delega a la
    conexión real.
    """

    def __init__(self, claves, real):
        self.claves = list(claves)
        self.real = real
        self.filas = []
        self.cur_real = None

    def cursor(self, dictionary=False, **kwargs):
        self.cur_real = (self.real.cursor(dictionary=dictionary, **kwargs)
                         if self.real is not None else None)
        return self

    def execute(self, sql, params=None):
        if 'usuario_permiso' in sql:
            self.cur_real = None
            if self.claves:
                self.filas = [{'activo': True, 'permiso': c,
                               'plantilla_sembrada': True}
                              for c in self.claves]
            else:
                self.filas = [{'activo': True, 'permiso': None,
                               'plantilla_sembrada': True}]
        elif self.cur_real is not None:
            self.cur_real.execute(sql, params)

    def fetchall(self):
        if self.cur_real is not None:
            return self.cur_real.fetchall()
        return self.filas

    def fetchone(self):
        if self.cur_real is not None:
            return self.cur_real.fetchone()
        return self.filas[0] if self.filas else None

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _cliente_con_rol(rol):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = rol
    return c


def test_sql_30dias_es_sintaxis_postgresql():
    assert "DATE_SUB" not in SQL_MOVIMIENTOS_30DIAS
    assert "INTERVAL '30 days'" in SQL_MOVIMIENTOS_30DIAS


def test_query_30dias_ejecuta_en_postgres_sin_error():
    with db_supabase.engine_tienda.connect() as c:
        filas = c.execute(text(SQL_MOVIMIENTOS_30DIAS)).fetchall()
    assert isinstance(filas, list)  # antes: ProgrammingError por DATE_SUB


def test_reportes_renderiza_la_seccion_de_resultados(monkeypatch):
    # El vendedor necesita el módulo 'reportes' (permiso por módulo): se
    # inyecta el resultado de la carga de permisos con un doble (patrón de
    # tests/test_permisos.py) para no depender de la BD Auth.
    conn_a = db_supabase.get_connection_auth()
    monkeypatch.setattr(modulo_app, 'get_connection_auth',
                        lambda: ConnPermisos(('reportes',), conn_a))
    try:
        r = _cliente_con_rol('vendedor').get('/reportes')
    finally:
        conn_a.close()
    assert r.status_code == 200
    assert 'Resultados' in r.get_data(as_text=True)  # oculta cuando la query fallaba


def test_ventas_hoy_cuenta_solo_pagos_confirmados():
    with db_supabase.engine_tienda.connect() as c:
        pagadas = float(c.execute(text(SQL_VENTAS_HOY_PAGADAS)).scalar() or 0)
        # Validación independiente del criterio (EXISTS, sin depender del
        # JOIN) y de la fórmula neta (descuento congelado incluido)
        esperado = float(c.execute(text(f"""
            SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0)
            FROM venta v
            JOIN detalle_venta d ON d.id_venta = v.id_venta
            WHERE (v.fecha AT TIME ZONE 'America/Lima')::date
                      = (NOW() AT TIME ZONE 'America/Lima')::date
              AND EXISTS (SELECT 1 FROM pago p
                          WHERE p.id_venta = v.id_venta AND p.estado = 'confirmado')
        """)).scalar() or 0)
        todas = float(c.execute(text(f"""
            SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0)
            FROM venta v
            JOIN detalle_venta d ON d.id_venta = v.id_venta
            WHERE (v.fecha AT TIME ZONE 'America/Lima')::date
                      = (NOW() AT TIME ZONE 'America/Lima')::date
        """)).scalar() or 0)
    assert pagadas == esperado            # criterio correcto (validado con EXISTS)
    assert pagadas <= todas               # nunca supera el total sin filtro
    assert "estado = 'confirmado'" in SQL_VENTAS_HOY_PAGADAS
    assert SQL_IMPORTE_LINEA.format(p='d.') in SQL_VENTAS_HOY_PAGADAS
