#!/usr/bin/env python
with open('tests/test_permisos.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = """            assert resp.status_code == 302, f'{desc}: esperado 302, got {resp.status_code}'
            assert resp.headers['Location'].endswith('/inicio_personal'), \\
                f'{desc}: expected /inicio_personal, got {resp.headers["Location"]}'"""

new = """            assert resp.status_code == 302, f'{desc}: esperado 302, got {resp.status_code}'
            location = resp.headers['Location']
            assert location.endswith('/inicio_personal'), \\
                f'{desc}: expected /inicio_personal, got {location}'"""

if old in content:
    content = content.replace(old, new)
    with open('tests/test_permisos.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: Fixed')
else:
    print('ERROR: Old string not found')
    # Show what's actually there around line 565
    lines = content.split('\n')
    for i in range(562, 570):
        print(f'{i+1}: {repr(lines[i])}')
EOF