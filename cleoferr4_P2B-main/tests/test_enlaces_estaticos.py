"""Los enlaces estaticos de las plantillas apuntan a rutas que existen.

Un href/action estatico (sin Jinja, sin parametros) que no esta en el
url_map es un 404 seguro: el boton se ve en pantalla pero no lleva a
ninguna parte (o a una pagina de error). El test recorre TODAS las
plantillas, extrae esos href/action y los contrasta con app.url_map.
"""
import re
from pathlib import Path

from app import app

RAIZ = Path(__file__).resolve().parent.parent / 'templates'

_PATRONES = (
    re.compile(r'\bhref\s*=\s*"([^"]*)"'),
    re.compile(r"\bhref\s*=\s*'([^']*)'"),
    re.compile(r'\baction\s*=\s*"([^"]*)"'),
    re.compile(r"\baction\s*=\s*'([^']*)'"),
)

# Los <script> se ignoran: dentro del JS un "action = '/ruta/' + id" arma
# una URL dinamica, no un enlace estatico de la pagina.
_SCRIPT = re.compile(r'<script\b.*?</script>', re.IGNORECASE | re.DOTALL)


def _regla_a_regex(regla):
    """Convierte una regla de Flask ('/alertas/resolver/<int:id>') en regex."""
    partes = []
    for parte in re.split(r'(<[^>]+>)', regla):
        if parte.startswith('<') and parte.endswith('>'):
            tipo = parte[1:-1].split(':')[0]
            partes.append(r'.+' if tipo == 'path' else r'[^/]+')
        else:
            partes.append(re.escape(parte))
    return re.compile('^' + ''.join(partes) + '$')


def _enlaces_estaticos(plantilla):
    """Valores href/action estaticos (locales) de una plantilla."""
    fuente = _SCRIPT.sub('', plantilla.read_text(encoding='utf-8'))
    vistos = set()
    for patron in _PATRONES:
        for m in patron.finditer(fuente):
            valor = m.group(1).strip()
            if not valor.startswith('/') or valor.startswith('//'):
                continue          # externos, anclas o javascript:
            if '{' in valor or '}' in valor:
                continue          # dinamicos (Jinja): no se comprueban aqui
            valor = valor.split('#')[0].split('?')[0]
            if not valor or valor in vistos:
                continue
            vistos.add(valor)
            yield valor


def test_los_enlaces_estaticos_de_las_plantillas_existen():
    patrones = [_regla_a_regex(str(regla.rule))
                for regla in app.url_map.iter_rules()]
    rotos = []
    for plantilla in sorted(RAIZ.rglob('*.html')):
        for valor in _enlaces_estaticos(plantilla):
            if not any(p.match(valor) for p in patrones):
                rotos.append(f'{plantilla.relative_to(RAIZ)}: {valor}')
    assert not rotos, ('enlaces a rutas que no existen:\n'
                        + '\n'.join(rotos))


def test_el_reporte_por_rango_apunta_a_una_ruta_real():
    """El formulario de /reportes (y el de la pagina del reporte) apuntan
    a la ruta real del reporte por rango, no a una URL inventada."""
    reglas = {str(regla.rule) for regla in app.url_map.iter_rules()}
    assert '/reportes/generar' in reglas
    for plantilla in ('reportes.html', 'reportes_rango.html'):
        fuente = (RAIZ / plantilla).read_text(encoding='utf-8')
        assert 'action="/reportes/generar"' in fuente, plantilla
