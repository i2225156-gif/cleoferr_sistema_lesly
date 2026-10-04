import sys
sys.path.insert(0, '.')
from app import get_connection_tienda

conn = get_connection_tienda()
cursor = conn.cursor()

# Mark order 50 as cancelled instead of deleting
cursor.execute("UPDATE venta SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta WHERE nombre = 'cancelado') WHERE id_venta = 50")
conn.commit()

# Verify
cursor.execute("SELECT v.id_venta, ev.nombre FROM venta v LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta WHERE v.id_venta IN (49, 50)")
remaining = cursor.fetchall()
print(f'Orders 49 and 50 status: {remaining}')

conn.close()