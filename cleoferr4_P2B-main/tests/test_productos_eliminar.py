"""Fix DELETE de producto: bloquear si tiene historial y sugerir desactivar.

SOLO LECTURA: se prueban los caminos que NO escriben (bloqueo, 404 y
permisos). El borrado exitoso exigiría escribir en BD y queda cubierto por
revisión, no por test.
"""
import db_supabase
from sqlalchemy import text

from app import app


def _admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 7
        s['rol'] = 'administrador'
    return c


def _producto_con_historial():
    with db_supabase.engine_tienda.connect() as c:
        row = c.execute(text(
            "SELECT id_producto FROM detalle_venta ORDER BY id_producto LIMIT 1")).fetchone()
    assert row, "la BD de pruebas no tiene productos con ventas"
    return row[0]


def test_producto_con_ventas_no_se_elimina_y_sugiere_desactivar():
    pid = _producto_con_historial()
    r = _admin().get(f'/productos/eliminar/{pid}', follow_redirects=True)
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert 'No se puede eliminar' in cuerpo
    assert 'desact' in cuerpo.lower()
    with db_supabase.engine_tienda.connect() as c:
        sigue = c.execute(text(
            "SELECT COUNT(*) FROM producto WHERE id_producto = :p"),
            {"p": pid}).scalar()
    assert sigue == 1  # no hubo borrado


def test_producto_inexistente_da_404():
    assert _admin().get('/productos/eliminar/999999').status_code == 404


def test_vendedor_no_puede_eliminar():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'vendedor'
    assert c.get('/productos/eliminar/1').status_code == 302
