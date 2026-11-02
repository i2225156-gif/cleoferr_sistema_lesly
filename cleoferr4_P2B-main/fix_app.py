#!/usr/bin/env python
# fix_app.py - Fix the string quoting issue in app.py around line 4055-4058

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The current broken string (from line 4055-4058 area)
# error=('Este correo ya tiene una cuenta activa. Inicia sesi on usa '
#        '<a href="/recuperar_contrasena" class="link-cliente">'
#        "¿Olvidaste tu contraseña? y cambia a la pestaña "
#        '<a href="/login" class="link-cliente">Iniciar Sesión</a>'),

# What we need:
# error=('Este correo ya tiene una cuenta activa. Inicia sesión o usa '
#        '<a href="/recuperar_contrasena" class="link-cliente">'
#        '¿Olvidaste tu contraseña?" y cambia a la pestaña '
#        '<a href="/login" class="link-cliente">Iniciar Sesión</a>'),

# Let me find and replace the exact broken pattern
old_pattern = "error=('Este correo ya tiene una cuenta activa. Inicia sesi\\n o usa '"
new_pattern = "error=('Este correo ya tiene una cuenta activa. Inicia sesión o usa '"

if old_pattern in content:
    content = content.replace(old_pattern, new_pattern)
    print(f'Replaced: {old_pattern[:50]}...')
else:
    print('Old pattern not found')
    # Let's just print what's around line 4055
    lines = content.split('\n')
    for i in range(4053, 4062):
        print(f'Line {i+1}: {lines[i][:80]}')

# Now fix the rest - the issue is the string continuation has wrong quote types
# Let me find the full error string and replace it completely

# Find the start of the error string
start_idx = content.find("error=('Este correo ya tiene una cuenta activa.")
if start_idx != -1:
    # Find where this string ends (the closing '),')
    end_pattern = "'),"
    end_idx = content.find(end_pattern, start_idx)
    if end_idx != -1:
        old_full = content[start_idx:end_idx + len(end_pattern)]
        new_full = """error=('Este correo ya tiene una cuenta activa. Inicia sesión o usa '
'<a href="/recuperar_contrasena" class="link-cliente">¿Olvidaste tu contraseña?</a> y cambia a la pestaña '
'<a href="/login" class="link-cliente">Iniciar Sesión</a>'),"""
        
        if old_full in content:
            content = content.replace(old_full, new_full)
            print(f'Replaced full pattern successfully')
        else:
            print('Full pattern not found, trying partial...')
            # Try replacing piece by piece
            pass
    else:
        print('End pattern not found')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')