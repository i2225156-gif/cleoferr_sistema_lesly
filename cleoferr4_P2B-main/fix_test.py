#!/usr/bin/env python
import re

with open('tests/test_permisos.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix line 565-566
old = """            assert resp.status_code == 302, f'{desc}: esperado 302, got {resp.status_code}'
            assert resp.headers['Location'].endswith('/inicio_personal'), \\
                f'{desc}: expected /inicio_personal, got {resp.headers["Location"]}""""

new = """            assert resp.status_code == 302, f'{desc}: esperado 302, got {resp.status_code}'
            location = resp.headers['Location']
            assert location.endswith('/inicio_personal'), \\
                f'{desc}: expected /inicio_personal, got {location}'"""

content = content.replace(old, new)

with open('tests/test_permisos.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed')