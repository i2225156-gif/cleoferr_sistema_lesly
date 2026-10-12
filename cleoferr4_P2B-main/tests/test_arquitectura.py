"""Regla A.2 (corrección 6): app.py y los módulos de negocio no importan la
implementación de un proveedor (simulada/culqi; simulado/real). Análisis de
imports con ast, no grep. Se permite importar SOLO base y registro (y el
paquete como tal) fuera de services/pasarela y services/sunat.
"""
import ast
from pathlib import Path

PROYECTO = Path(__file__).resolve().parent.parent

_PROHIBIDAS = (
    "services.pasarela.simulada",
    "services.pasarela.culqi",
    "services.sunat.simulado",
    "services.sunat.real",
)

_DIRS_EXCLUIDAS = {"venv", "_archivo", "__pycache__", ".pytest_cache", "tests",
                   "pasarela", "sunat"}


def _archivos_a_escanear():
    for arch in PROYECTO.rglob("*.py"):
        rel = arch.relative_to(PROYECTO)
        if any(parte in _DIRS_EXCLUIDAS for parte in rel.parts):
            continue
        yield arch


def _imports(arbol):
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                yield alias.name
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.module:
                yield nodo.module
                for alias in nodo.names:
                    yield f"{nodo.module}.{alias.name}"


def test_negocio_no_importa_implementaciones_de_proveedor():
    violaciones = []
    escaneados = 0
    for arch in _archivos_a_escanear():
        arbol = ast.parse(arch.read_bytes(), filename=str(arch))
        escaneados += 1
        for modulo in _imports(arbol):
            if modulo.startswith(_PROHIBIDAS):
                violaciones.append(f"{arch.relative_to(PROYECTO)} -> {modulo}")
    assert escaneados > 0, "no se escaneó ningún archivo: revisar _DIRS_EXCLUIDAS"
    assert not violaciones, "Imports prohibidos:\n" + "\n".join(violaciones)
