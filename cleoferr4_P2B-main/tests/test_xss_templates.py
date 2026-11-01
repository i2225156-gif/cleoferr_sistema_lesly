"""XSS en las plantillas del carrito y del POS (nombres de producto/cliente).

El nombre de un producto o de un cliente es texto libre: si se interpola
dentro de un innerHTML, un nombre como "<img src=x onerror=alert(1)>"
acaba siendo MARCADO VIVO (el navegador lo ejecuta).

Dos comprobaciones:
- (a) de fuente: en carrito.html y venta_form.html ningun texto libre se
  interpola dentro de un innerHTML sin pasar por la funcion de escape.
- (b) de plantilla: el JS de la plantilla se ejecuta con un nombre
  malicioso y se comprueba que NO queda como HTML activo (no se dispara
  ningun manejador y el nombre se ve como texto).

El escenario (b) usa un DOM minimo en Node (tests/js/xss_harness.mjs): sin
Node el test se omite (la comprobacion (a) sigue vigente).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import render_template

from app import app

RAIZ = Path(__file__).resolve().parent.parent
TEMPLATES = RAIZ / 'templates'
HARNESS = Path(__file__).resolve().parent / 'js' / 'xss_harness.mjs'

PAYLOAD = '<img src=x onerror=alert(1)>'
ID_PRODUCTO = 4242

# Identificadores que llevan texto libre (nombre de producto, de cliente,
# razon social, documento...): NUNCA deben entrar crudos en un innerHTML.
DATOS_LIBRES = {
    'nombre', 'nombres', 'nombreCompletoDni', 'razonSocial', 'razonSocialActual',
    'renNombres', 'renApellidos', 'completo', 'motivo', 'descripcion',
    'direccion', 'apellido', 'telefono', 'correo', 'email', 'ruc', 'dni',
}


# ── (a) comprobacion de fuente ──────────────────────────────────────────

def _saltar_cadena(fuente, i, delim):
    """Devuelve el indice tras la cadena que empieza en i."""
    i += 1
    while i < len(fuente):
        if fuente[i] == '\\':
            i += 2
            continue
        if fuente[i] == delim:
            return i + 1
        i += 1
    return i


def _saltar_plantilla(fuente, i):
    """Devuelve el indice tras la plantilla que empieza en i (backtick)."""
    i += 1
    while i < len(fuente):
        c = fuente[i]
        if c == '\\':
            i += 2
            continue
        if c == '`':
            return i + 1
        if c == '$' and i + 1 < len(fuente) and fuente[i + 1] == '{':
            i = _saltar_expresion(fuente, i + 2, hasta='}')
            continue
        i += 1
    return i


def _saltar_expresion(fuente, i, hasta=';'):
    """Devuelve el indice tras la expresion JS que empieza en i."""
    profundidad = 0
    while i < len(fuente):
        c = fuente[i]
        if c in ('"', "'"):
            i = _saltar_cadena(fuente, i, c)
            continue
        if c == '`':
            i = _saltar_plantilla(fuente, i)
            continue
        if c == hasta and profundidad <= 0:
            return i
        if c in '([{':
            profundidad += 1
        elif c in ')]}':
            profundidad -= 1
        i += 1
    return i


def _asignaciones_plantilla(fuente, ident):
    """Asignaciones JS donde `ident` recibe una plantilla (constructor de
    HTML). Solo cuenta el codigo: un atributo HTML (`id="..."`) no es una
    asignacion."""
    patrones = (
        r'(?<![\w$.])(?:let|const|var)\s+' + re.escape(ident) + r'\s*=\s*',
        r'(?<![\w$.])' + re.escape(ident) + r'\s*\+=\s*',
    )
    for patron in patrones:
        for a in re.finditer(patron, fuente):
            fin = _saltar_expresion(fuente, a.end())
            rhs = fuente[a.end():fin]
            if '`' in rhs:
                yield rhs


def _expresiones_innerhtml(fuente):
    """Expresiones JS que alimentan un innerHTML (siguiendo las variables)."""
    expresiones = []
    vistas = set()
    variables = set()

    def _agregar(expr):
        expr = expr.strip()
        if not expr or expr in vistas:
            return
        vistas.add(expr)
        expresiones.append(expr)
        for ident in set(re.findall(r'[A-Za-z_$][\w$]*', _solo_codigo(expr))):
            if ident in variables:
                continue
            # Solo se siguen las variables que CONSTRUYEN HTML (se asignan
            # con una plantilla); las demas (un nombre normalizado, un
            # total...) no alimentan el innerHTML.
            rhos = list(_asignaciones_plantilla(fuente, ident))
            if rhos:
                variables.add(ident)
                for rhs in rhos:
                    _agregar(rhs)

    for m in re.finditer(r'\.innerHTML\s*=\s*', fuente):
        fin = _saltar_expresion(fuente, m.end())
        _agregar(fuente[m.end():fin])
    return expresiones


def _solo_codigo(expresion):
    """La expresión sin el TEXTO de las cadenas: solo el código JS.

    Asi el texto de un aviso ("...a tu nombre") no se confunde con una
    variable; de las plantillas de plantilla solo queda el codigo de las
    interpolaciones.
    """
    salida = []
    i = 0
    while i < len(expresion):
        c = expresion[i]
        if c in ('"', "'"):
            i = _saltar_cadena(expresion, i, c)
            continue
        if c == '`':
            j = i + 1
            while j < len(expresion):
                if expresion[j] == '\\':
                    j += 2
                    continue
                if expresion[j] == '`':
                    j += 1
                    break
                if expresion[j] == '$' and j + 1 < len(expresion) and expresion[j + 1] == '{':
                    fin = _saltar_expresion(expresion, j + 2, hasta='}')
                    salida.append(expresion[j + 2:fin] + ';')
                    j = fin
                    continue
                j += 1
            i = j
            continue
        salida.append(c)
        i += 1
    return ''.join(salida)


_CADENA = re.compile(r'(?:[A-Za-z_$][\w$]*\s*\.\s*)*[A-Za-z_$][\w$]*')


def _datos_libres_sin_escapar(expresion):
    """Datos libres que aparecen en la expresion SIN estar dentro de esc(...)."""
    codigo = _solo_codigo(expresion)
    sin_escapar = []
    for m in _CADENA.finditer(codigo):
        cadena = m.group(0)
        if cadena.split('.')[-1].strip() not in DATOS_LIBRES:
            continue
        if not codigo[:m.start()].rstrip().endswith('esc('):
            sin_escapar.append(cadena)
    return sin_escapar


@pytest.mark.parametrize('plantilla', ['carrito.html', 'venta_form.html'])
def test_ningun_texto_libre_se_interpola_en_un_innerhtml(plantilla):
    fuente = (TEMPLATES / plantilla).read_text(encoding='utf-8')
    assert '.innerHTML' in fuente, 'la plantilla ya no usa innerHTML'
    problemas = []
    for expresion in _expresiones_innerhtml(fuente):
        for ident in _datos_libres_sin_escapar(expresion):
            problemas.append(f'{plantilla}: {ident} sin esc() en -> {expresion[:90]}')
    assert not problemas, '\n'.join(problemas)


@pytest.mark.parametrize('plantilla', ['carrito.html', 'venta_form.html'])
def test_la_fuente_usa_una_unica_funcion_de_escape(plantilla):
    """El escape vive en base.html (una sola funcion para todas las
    paginas) y las dos plantillas lo usan."""
    fuente = (TEMPLATES / plantilla).read_text(encoding='utf-8')
    assert 'esc(' in fuente, f'{plantilla} no usa la funcion de escape'
    base = (TEMPLATES / 'base.html').read_text(encoding='utf-8')
    assert base.count('function esc(') == 1, 'la funcion de escape debe ser una sola'


# ── (b) comprobacion de plantilla ───────────────────────────────────────

def _render(plantilla, **contexto):
    with app.test_request_context('/'):
        return render_template(plantilla, **contexto)


def _correr(escenario, html, **extra):
    node = shutil.which('node')
    if not node:
        pytest.skip('node no disponible para ejecutar el JS de la plantilla')
    config = {'html': html, 'escenario': escenario, 'payload': PAYLOAD}
    config.update(extra)
    proc = subprocess.run([node, str(HARNESS)], input=json.dumps(config),
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _elementos(nodos):
    for nodo in nodos:
        if 'tag' in nodo:
            yield nodo
            yield from _elementos(nodo.get('hijos', []))


def _comprobar_sin_html_activo(resultado, objetivo):
    assert resultado['errores'] == [], resultado['errores']
    assert resultado['alertas'] == [], 'el payload se ejecutó como HTML activo'
    nodos = list(_elementos(resultado['arboles'][objetivo]))
    assert not [n for n in nodos if n['tag'] == 'img'], \
        'el nombre malicioso quedó como <img> (HTML activo)'
    assert not [n for n in nodos if 'onerror' in n.get('attrs', {})], \
        'el nombre malicioso dejó un manejador onerror activo'
    assert PAYLOAD in resultado['texto'][objetivo], \
        'el nombre debe seguir viéndose (como texto)'


def test_carrito_la_tabla_no_convierte_el_nombre_en_html():
    html = _render('carrito.html', csrf_token='token-test')
    resultado = _correr('carrito_tabla', html, localStorage={
        'carrito_cleoferr': json.dumps([
            {'id': 1, 'nombre': PAYLOAD, 'precio': 10.0, 'cantidad': 2}])
    })
    _comprobar_sin_html_activo(resultado, 'tbodyCarrito')


def test_carrito_el_resumen_no_convierte_el_nombre_en_html():
    html = _render('carrito.html', csrf_token='token-test')
    resultado = _correr('carrito_resumen', html, localStorage={
        'carrito_cleoferr': json.dumps([
            {'id': 1, 'nombre': PAYLOAD, 'precio': 10.0, 'cantidad': 2}])
    })
    _comprobar_sin_html_activo(resultado, 'resumenConfirmar')


def _html_pos():
    return _render('venta_form.html', csrf_token='token-test', productos=[
        {'id_producto': ID_PRODUCTO, 'nombre': PAYLOAD, 'precio': 10.0, 'stock': 5},
    ])


def test_pos_el_catalogo_no_convierte_el_nombre_en_html():
    resultado = _correr('pos_catalogo', _html_pos(), productoId=ID_PRODUCTO)
    _comprobar_sin_html_activo(resultado, 'gridProductos')


def test_pos_el_ticket_no_convierte_el_nombre_en_html():
    resultado = _correr('pos_ticket', _html_pos(), productoId=ID_PRODUCTO)
    _comprobar_sin_html_activo(resultado, 'listaItems')


def test_pos_el_cliente_no_convierte_el_nombre_en_html():
    resultado = _correr('pos_cliente', _html_pos(), productoId=ID_PRODUCTO)
    _comprobar_sin_html_activo(resultado, 'compResumen')
