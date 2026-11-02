"""Validación de teléfono de CLIENTE y PROVEEDOR (sin BD).

telefono() es OPCIONAL: vacío o solo espacios se acepta como ausente
(None, None). Si viene, se le quitan los espacios y lo que resta debe
ser solo dígitos y de 7 a 9 caracteres: si no, se rechaza con el
mensaje del panel («El teléfono solo debe contener números (7 a 9
dígitos).»). Con espacios intermedios ('999 888 777') se acepta y se
guarda SIN espacios ('999888777').

Solo se ejercen funciones puras de services.validacion: sin Flask,
sin BD y sin fixtures de rollback.
"""
import pytest

from services.validacion import telefono

MENSAJE = 'El teléfono solo debe contener números (7 a 9 dígitos).'



# ── opcional: vacío o solo espacios ──────────────────────────────

@pytest.mark.parametrize('valor', [None, '', '   ', '\t'])
def test_telefono_vacio_aceptado_como_ausente(valor):
    assert telefono(valor) == (None, None)


# ── válidos ──────────────────────────────────────────────────────

def test_telefono_nueve_digitos_aceptado():
    assert telefono('987654321') == ('987654321', None)


def test_telefono_siete_digitos_aceptado():
    assert telefono('1234567') == ('1234567', None)


def test_telefono_ocho_digitos_aceptado():
    assert telefono('  12345678 ') == ('12345678', None)


def test_telefono_con_espacios_intermedios_se_limpia():
    assert telefono('999 888 777') == ('999888777', None)


# ── rechazados ───────────────────────────────────────────────────

@pytest.mark.parametrize('valor', ['123456', '1234567890', '999 88 7',
                                   '98a765432', '98-765-432', '+987654321',
                                   '98765.321', 'áéí56789'])
def test_telefono_invalido_rechazado(valor):
    assert telefono(valor) == (None, MENSAJE)


def test_telefono_veintiun_digitos_rechazado():
    """Caso que antes pasaba la regla de «20 caracteres»: ahora falla
    porque supera los 9 dígitos."""
    assert telefono('9' * 21) == (None, MENSAJE)
