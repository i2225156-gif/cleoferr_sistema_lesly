import sys
sys.path.insert(0, '.')
from app import get_connection_tienda

conn = get_connection_tienda()
cursor = conn.cursor(dictionary=True)

# Check products in order 50
cursor.execute('''
    SELECT d.id_producto, p.nombre, d.cantidad, d.precio_unitario, (d.cantidad * d.precio_unitario) as subtotal
    FROM detalle_venta d
    JOIN producto p ON d.id_producto = p.id_producto
    WHERE d.id_venta = 50
''')
products_50 = cursor.fetchall()
print('=== Order 50 products ===')
for p in products_50:
    print(f'  Producto: {p["nombre"]}, qty={p["cantidad"]}, price={p["precio_unitario"]}, subtotal={p["subtotal"]}')

# Check products in order 49
cursor.execute('''
    SELECT d.id_producto, p.nombre, d.cantidad, d.precio_unitario, (d.cantidad * d.precio_unitario) as subtotal
    FROM detalle_venta d
    JOIN producto p ON d.id_producto = p.id_producto
    WHERE d.id_venta = 49
''')
products_49 = cursor.fetchall()
print()
print('=== Order 49 products ===')
for p in products_49:
    print(f'  Producto: {p["nombre"]}, qty={p["cantidad"]}, price={p["precio_unitario"]}, subtotal={p["subtotal"]}')

# Check total
cursor.execute('SELECT SUM(d.cantidad * d.precio_unitario) as total FROM detalle_venta d WHERE d.id_venta = 50')
total50 = cursor.fetchone()['total']
cursor.execute('SELECT SUM(d.cantidad * d.precio_unitario) as total FROM detalle_venta d WHERE d.id_venta = 49')
total49 = cursor.fetchone()['total']
print(f'\nOrder 50 total: {total50}')
print(f'Order 49 total: {total49}')

conn.close()