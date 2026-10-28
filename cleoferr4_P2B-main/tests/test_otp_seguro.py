"""Códigos OTP de 6 dígitos generados con secrets (criptográficamente seguros).

El módulo random no es seguro para criptografía: los OTP se generan ahora
con secrets.randbelow(1000000). Test sin BD: verifica el formato de 6
dígitos de la generación y que app.py ya no use random para los códigos.
"""
import re
import secrets
from pathlib import Path

_APP_PY = Path(__file__).resolve().parent.parent / 'app.py'


def _generar_otp():
    return f"{secrets.randbelow(1000000):06d}"


def test_otp_tiene_6_digitos():
    for _ in range(1000):
        codigo = _generar_otp()
        assert re.fullmatch(r'\d{6}', codigo), f"OTP invalido: {codigo!r}"


def test_otp_puede_tener_ceros_a_la_izquierda():
    codigo = f"{secrets.randbelow(1):06d}"
    assert codigo == '000000'


def test_app_usa_secrets_para_otp():
    fuente = _APP_PY.read_text(encoding='utf-8')
    assert 'secrets.randbelow(1000000):06d' in fuente
    assert 'random.randint' not in fuente
