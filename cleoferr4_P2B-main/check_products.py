import sys
sys.path.insert(0, '.')
from app import get_connection_tienda

conn = get_connection_tienda()
cursor = conn.cursor(dictionary=True)

# Check products with stock < 15
cursor.execute("""
    SELECT id_producto, nombre, stock, stock_minimo, precio
    FROM producto WHERE stock < 15 ORDER BY stock ASC
""")
bajo_stock = cursor.fetchall()
print('=== Products with stock < 15 ===')
for p in bajo_stock:
    print(f"  {p['id_producto']}: {p['nombre']} - stock: {p['stock']} (min: {p['stock_minimo']})")

print()

# Check all products
cursor.execute('SELECT id_producto, nombre, stock, stock_minimo FROM producto ORDER BY id_producto')
all_products = cursor.fetchall()
print('=== All products ===')
for p in all_products:
    print(f"  {p['id_producto']}: {p['nombre']} - stock: {p['stock']} (min: {p['stock_minimo']})")

conn.close()