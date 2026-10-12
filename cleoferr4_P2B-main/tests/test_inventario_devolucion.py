"""B2: devolver_stock_venta es idempotente (sin tocar la BD: cursor falso)."""
from services.inventario import devolver_stock_venta


class FakeCursor:
    def __init__(self, resultados):
        self.resultados = list(resultados)
        self.sentencias = []

    def execute(self, sql, params=None):
        self.sentencias.append((sql, params))

    def fetchall(self):
        return self.resultados.pop(0)

    def fetchone(self):
        return self.resultados.pop(0)


def test_devuelve_stock_y_registra_movimiento():
    cur = FakeCursor([
        [(7, 2, 10.5)],   # detalle_venta
        None,             # marca no existe
        (15,),            # UPDATE producto RETURNING stock
    ])
    assert devolver_stock_venta(cur, 99) == 1
    sqls = " | ".join(s for s, _ in cur.sentencias)
    assert "UPDATE producto" in sqls
    assert "INSERT INTO inventario_movimiento" in sqls
    assert "Devolución stock venta #99 (producto #7)" in str(cur.sentencias[3][1])


def test_es_idempotente_cuando_la_marca_ya_existe():
    cur = FakeCursor([
        [(7, 2, 10.5)],   # detalle_venta
        (1,),             # marca YA existe
    ])
    assert devolver_stock_venta(cur, 99) == 0
    assert all("UPDATE producto" not in s for s, _ in cur.sentencias)


def test_venta_sin_detalle_no_hace_nada():
    cur = FakeCursor([[]])
    assert devolver_stock_venta(cur, 99) == 0
    assert len(cur.sentencias) == 1
