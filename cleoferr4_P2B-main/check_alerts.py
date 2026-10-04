import sys
sys.path.insert(0, '.')
from app import get_connection_tienda

conn = get_connection_tienda()
cursor = conn.cursor(dictionary=True)

# Check distinct id_tipo_alerta values
cursor.execute('SELECT DISTINCT id_tipo_alerta, COUNT(*) as cnt FROM alerta GROUP BY id_tipo_alerta')
print('=== Alert types ===')
for row in cursor.fetchall():
    print(row)

print()

# Count unresolved by type
cursor.execute('SELECT id_tipo_alerta, COUNT(*) as cnt FROM alerta WHERE resuelta = FALSE GROUP BY id_tipo_alerta')
print('=== Unresolved by type ===')
for row in cursor.fetchall():
    print(row)

# Also check total unresolved
cursor.execute('SELECT COUNT(*) as cnt FROM alerta WHERE resuelta = FALSE')
print('=== Total unresolved ===')
for row in cursor.fetchall():
    print(row)

# Check total alerts
cursor.execute('SELECT COUNT(*) as cnt FROM alerta')
print('=== Total alerts ===')
for row in cursor.fetchall():
    print(row)

conn.close()