import sys
sys.path.insert(0, '.')
from app import app, get_connection_tienda
import re

print('=== END-TO-END VERIFICATION ===')
print()

with app.test_client() as client:
    # Test the /productos route
    resp = client.get('/productos')
    print('GET /productos status:', resp.status_code)
    
    # Check that alertas_count is 3 (not 4)
    html = resp.get_data(as_text=True)
    print('alertas_count in rendered HTML:', end=' ')
    match = re.search(r'stat-value\">(\d+)', html)
    if match:
        print(match.group(1))
    else:
        print('Not found in HTML')
    
    # Check that bajo_stock has 3 items
    match2 = re.search(r'(\d+) producto\(s\) con stock insuficiente', html)
    if match2:
        print('Productos con stock insuficiente:', match2.group(1))
    
    print()
    print('=== Query verification ===')
    
    # Verify the new query logic
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    
    # New query (should return 3)
    cursor.execute('''
        SELECT COUNT(*) AS cnt FROM alerta
        WHERE resuelta = FALSE AND id_tipo_alerta = 1
        AND id_producto IN (SELECT id_producto FROM producto WHERE stock < 15)
    ''')
    new_count = cursor.fetchone()['cnt']
    print(f'New alertas count query: {new_count}')
    
    # Old query (would return 4 - the bug)
    cursor.execute('SELECT COUNT(*) AS cnt FROM alerta WHERE resuelta = FALSE')
    old_count = cursor.fetchone()['cnt']
    print(f'Old alertas count query: {old_count}')
    
    # Product count with stock < 15
    cursor.execute('SELECT COUNT(*) AS cnt FROM producto WHERE stock < 15')
    product_count = cursor.fetchone()['cnt']
    print(f'Productos con stock < 15: {product_count}')
    
    conn.close()
    
print()
print('=== UNIT TESTS ===')

# Run the unit tests
import subprocess
result = subprocess.run(['python', '-m', 'pytest', 'test_alertas_stock.py', '-v'], 
                       capture_output=True, text=True, cwd='.')
print(result.stdout)
if result.stderr:
    print('STDERR:', result.stderr)