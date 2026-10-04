import sys
sys.path.insert(0, '.')
from app import app, get_connection_tienda
import re

print('=== FINAL END-TO-END VERIFICATION ===')
print()

with app.test_client() as client:
    resp = client.get('/productos')
    html = resp.get_data(as_text=True)
    
    # Check alertas_count
    alert_match = re.search(r'stat-value\">(\d+)', html)
    if alert_match:
        count = alert_match.group(1)
        print(f'Dashboard alertas_count: {count}')
        assert count == '3', f'Expected 3, got {count}'
        print('  OK Alertas count correctly shows 3')
    
    # Check productos con stock insuficiente
    stock_match = re.search(r'(\d+) producto\(s\) con stock insuficiente', html)
    if stock_match:
        count = stock_match.group(1)
        print(f'Productos con stock insuficiente: {count}')
        assert count == '3', f'Expected 3, got {count}'
        print('  OK Productos con stock insuficiente correctly shows 3')
    
    print()
    print('=== Query verification ===')
    
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    
    # New query returns 3
    cursor.execute('''
        SELECT COUNT(*) AS cnt FROM alerta
        WHERE resuelta = FALSE AND id_tipo_alerta = 1
        AND id_producto IN (SELECT id_producto FROM producto WHERE stock < 15)
    ''')
    new_count = cursor.fetchone()['cnt']
    print(f'New alertas count query: {new_count}')
    assert new_count == 3, f'Expected 3, got {new_count}'
    
    # Old query would return 4
    cursor.execute('SELECT COUNT(*) AS cnt FROM alerta WHERE resuelta = FALSE')
    old_count = cursor.fetchone()['cnt']
    print(f'Old alertas count query: {old_count}')
    
    # Product count
    cursor.execute('SELECT COUNT(*) AS cnt FROM producto WHERE stock < 15')
    product_count = cursor.fetchone()['cnt']
    print(f'Productos con stock < 15: {product_count}')
    assert product_count == 3
    
    # Final count verification
    cursor.execute('''
        SELECT COUNT(*) AS cnt FROM alerta
        WHERE resuelta = FALSE AND id_tipo_alerta = 1
        AND id_producto IN (SELECT id_producto FROM producto WHERE stock < 15)
    ''')
    final_count = cursor.fetchone()['cnt']
    print(f'Final alertas count: {final_count}')
    assert final_count == 3
    
    conn.close()
    
print()
print('=== UNIT TESTS ===')

import subprocess
result = subprocess.run(['python', '-m', 'pytest', 'test_alertas_stock.py', '-v'], 
                       capture_output=True, text=True, cwd='.')
print(result.stdout)
if result.returncode == 0:
    print('  All 3 unit tests PASSED')
else:
    print('  Unit tests FAILED')
    print(result.stderr)