#!/usr/bin/env python
# fix_test.py - Fix the f-string issue in test_permisos.py

with open('tests/test_permisos.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and fix the problematic lines (around line 565-566)
# Line 565 (index 564): assert resp.status_code == 302, f'{desc}: esperado 302, got {resp.status_code}'
# Line 566 (index 565): assert resp.headers['Location'].endswith('/inicio_personal'), \
# Line 567 (index 566):     f'{desc}: expected /inicio_personal, got {resp.headers["Location"]}'

new_lines = []
for i, line in enumerate(lines):
    stripped = line.strip()
    # Fix line 566: change assert resp.headers['Location'].endswith to location = resp.headers['Location']
    if i == 565 and "resp.headers['Location'].endswith" in stripped:
        new_lines.append("            location = resp.headers['Location']\n")
    # Fix line 567: change the continuation assertion
    elif i == 566 and "endswith('/inicio_personal')" in stripped and "resp.headers" in stripped:
        new_lines.append("            assert location.endswith('/inicio_personal'), \\\n")
    # Fix line 568: the f-string with resp.headers["Location"]
    elif i == 567 and 'resp.headers["Location"]' in stripped:
        new_lines.append("                f'{desc}: expected /inicio_personal, got {location}'\n")
    else:
        new_lines.append(line)

with open('tests/test_permisos.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('Fix applied successfully')