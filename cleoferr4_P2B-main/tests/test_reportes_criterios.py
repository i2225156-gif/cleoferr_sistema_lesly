"""Fixes de reportes: DATE_SUB -> PostgreSQL y ventas_hoy solo con pago confirmado.

Contra la BD real pero SOLO LECTURA (SELECT). La sección "Resultados" de
reportes.html:82-84 solo se renderiza si la consulta de 30 días no lanzó
excepción: antes del fix, DATE_SUB (MySQL) la vaciaba siempre.
"""
import db_supabase
from sqlalchemy import text

from app import SQL_MOVIMIENTOS_30DIAS, SQL_VENTAS_HOY_PAGADAS, app
from services.ofertas import SQL_IMPORTE_LINEA


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


def test_reportes_renderiza_la_seccion_de_resultados():
    r = _cliente_con_rol('vendedor').get('/reportes')
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
