import sys
sys.path.insert(0, '.')
from app import get_connection_tienda

conn = get_connection_tienda()
cursor = conn.cursor(dictionary=True)

# Check for duplicates - just list orders with date and total
cursor.execute('''
    SELECT v.id_venta, v.fecha, COALESCE(SUM(d.cantidad * d.precio_unitario), 0) as total
    FROM venta v
    JOIN detalle_venta d ON v.id_venta = d.id_venta
    GROUP BY v.id_venta, v.fecha
    ORDER BY v.fecha DESC
    LIMIT 30
''')
rows = cursor.fetchall()
print('=== Recent orders (last 30) ===')
for r in rows:
    rid = r['id_venta']
    rf = r['fecha']
    rt = r['total']
    print(f'  id: {rid}, fecha: {rf}, total: {rt}')

# Check for same total+date combos
cursor.execute('''
    SELECT fecha, total, COUNT(*) as cnt
    FROM (
        SELECT v.id_venta, v.fecha, COALESCE(SUM(d.cantidad * d.precio_unitario), 0) as total
        FROM venta v
        JOIN detalle_venta d ON v.id_venta = d.id_venta
        GROUP BY v.id_venta, v.fecha
    )
    GROUP BY fecha, total
    ORDER BY cnt DESC
    LIMIT 10
''')
dupes = cursor.fetchall()
print()
print('=== Orders with same date+total (top 10) ===')
for d in dupes:
    fd = d['fecha']
    td = d['total']
    cd = d['cnt']
    print(f'  fecha: {fd}, total: {td}, count: {cd}')

conn.close()