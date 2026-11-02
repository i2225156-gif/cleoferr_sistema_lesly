#!/usr/bin/env python
with open('tests/test_permisos.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix lines 566-567 (0-indexed: 565-566)
# These are the problematic lines with the continuation

lines[565] = '            location = resp.headers["Location"]\n'
lines[566] = '            assert location.endswith("/inicio_personal"), \\\n                f\'{desc}: expected /inicio_personal, got {location}\'\n'

with open('tests/test_permisos.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed')