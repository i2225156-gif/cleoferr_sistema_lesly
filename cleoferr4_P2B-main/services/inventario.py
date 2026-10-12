"""Negocio de inventario: helpers transaccionales de stock (Fase 2.1)."""

_MARCA_DEVOLUCION = "Devolución stock venta #{id_venta} (producto #{id_producto})"


def _valor(fila, clave, indice):
    """fila puede ser tupla (cursor normal) o dict (cursor dictionary=True)."""
    if isinstance(fila, dict):
        return fila[clave]
    return fila[indice]


def devolver_stock_venta(cursor, id_venta):
    """Devuelve al stock los productos de una venta cancelada. Idempotente.

    Marca de idempotencia: inventario_movimiento.observacion con el texto
    'Devolución stock venta #<id_venta> (producto #<id_producto>)'. Si ya
    existe el movimiento de entrada para ese par venta/producto, no se repite.
    El caller garantiza además que la venta acaba de pasar a 'cancelado'
    (UPDATE ... WHERE estado <> 'cancelado' RETURNING), de modo que solo una
    transacción ejecuta la devolución.

    Debe llamarse con la MISMA transacción que actualiza la venta.
    Devuelve la cantidad de productos a los que se repuso stock.
    """
    cursor.execute(
        "SELECT id_producto, cantidad, precio_unitario FROM detalle_venta WHERE id_venta = %s",
        (id_venta,))
    filas = cursor.fetchall()
    if not filas:
        return 0
    devueltos = 0
    for fila in filas:
        id_producto = _valor(fila, 'id_producto', 0)
        cantidad = _valor(fila, 'cantidad', 1)
        precio = _valor(fila, 'precio_unitario', 2)
        marca = _MARCA_DEVOLUCION.format(id_venta=id_venta, id_producto=id_producto)
        cursor.execute(
            "SELECT 1 FROM inventario_movimiento WHERE observacion = %s LIMIT 1", (marca,))
        if cursor.fetchone():
            continue
        cursor.execute(
            "UPDATE producto SET stock = stock + %s WHERE id_producto = %s RETURNING stock",
            (cantidad, id_producto))
        nuevo_stock = _valor(cursor.fetchone(), 'stock', 0)
        cursor.execute("""
            INSERT INTO inventario_movimiento
                (tipo, id_producto, id_proveedor, cantidad, precio_unitario,
                 observacion, id_usuario, stock_resultante)
            VALUES ('entrada', %s, NULL, %s, %s, %s, NULL, %s)
        """, (id_producto, cantidad, precio, marca, nuevo_stock))
        devueltos += 1
    return devueltos
