import sys
sys.path.insert(0, '.')
from app import get_connection_tienda

conn = get_connection_tienda()
cursor = conn.cursor(dictionary=True)

# Check all alert details
cursor.execute('SELECT * FROM alerta ORDER BY fecha DESC')
alerts = cursor.fetchall()
print('=== All alerts ===')
for a in alerts:
    print(f"  id: {a['id_alerta']}, id_tipo: {a['id_tipo_alerta']}, id_producto: {a['id_producto']}, resuelta: {a['resuelta']}, desc: {a['descripcion']}, prioridad: {a['prioridad']}, fecha: {a['fecha']}")

print()

# Check which products each alert refers to
for a in alerts:
    cursor.execute('SELECT nombre FROM producto WHERE id_producto = %s', (a['id_producto'],))
    prod = cursor.fetchone()
    print(f"  Alert {a['id_alerta']} refers to product: {prod['nombre'] if prod else 'NOT FOUND (id=' + str(a['id_producto']) + ')'}")

conn.close()