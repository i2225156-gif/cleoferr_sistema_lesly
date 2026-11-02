"""Tramo 1: fábrica crear_pasarela (valor inválido y credenciales faltantes)."""
import logging
from decimal import Decimal

import pytest

from services.pasarela import registro
from services.pasarela.base import PagoSolicitud
from services.pasarela.gateway import PaymentGatewayError
from services.pasarela.registro import crear_pasarela

# config.py lee primero el nombre PAYMENT_* y luego PAGO_*: se limpian ambos
# para que el entorno de la máquina no enmascare el caso que se prueba.
_VARIABLES_PAGO = (
    "PAYMENT_PROVIDER", "PAGO_PROVEEDOR",
    "PAYMENT_SIMULATED_WEBHOOK_SECRET", "PAGO_SIMULADA_WEBHOOK_SECRETO",
    "PAYMENT_WEBHOOK_TOLERANCIA_SEG", "PAGO_WEBHOOK_TOLERANCIA_SEG",
    "PAYMENT_MODO", "PAGO_MODO",
    "PAYMENT_CULQI_LLAVE_PUBLICA", "PAGO_CULQI_LLAVE_PUBLICA",
    "PAYMENT_CULQI_SECRETA", "PAGO_CULQI_SECRETA",
)


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    for var in _VARIABLES_PAGO:
        monkeypatch.delenv(var, raising=False)


def test_proveedor_desconocido_lanza_error():
    with pytest.raises(PaymentGatewayError) as ei:
        crear_pasarela("stripe")
    assert "simulated, culqi" in str(ei.value)


def test_simulada_sin_secreto_lanza_error(monkeypatch):
    monkeypatch.setenv("PAGO_PROVEEDOR", "simulada")
    with pytest.raises(PaymentGatewayError) as ei:
        crear_pasarela()
    assert "PAGO_SIMULADA_WEBHOOK_SECRETO" in str(ei.value)


def test_simulada_con_secreto_de_ejemplo_lanza_error(monkeypatch):
    monkeypatch.setenv("PAGO_PROVEEDOR", "simulada")
    monkeypatch.setenv("PAGO_SIMULADA_WEBHOOK_SECRETO", registro._SECRETO_EJEMPLO)
    with pytest.raises(PaymentGatewayError) as ei:
        crear_pasarela()
    assert "valor de ejemplo" in str(ei.value)


def test_simulada_ok(monkeypatch):
    monkeypatch.setenv("PAGO_PROVEEDOR", "simulada")
    monkeypatch.setenv("PAGO_SIMULADA_WEBHOOK_SECRETO", "secreto-real-solo-para-test")
    monkeypatch.setenv("PAGO_WEBHOOK_TOLERANCIA_SEG", "300")
    p = crear_pasarela()
    assert p.nombre == "simulated"
    assert p.modo == "simulado"


def test_culqi_sin_credenciales_usa_simulada_con_advertencia(monkeypatch, caplog):
    # Contrato actual: sin llaves no se entrega un Culqi roto, se usa la
    # simulada y se avisa en el log qué variables faltan.
    monkeypatch.setenv("PAGO_SIMULADA_WEBHOOK_SECRETO", "secreto-real-solo-para-test")
    with caplog.at_level(logging.WARNING, logger=registro.__name__):
        p = crear_pasarela("culqi")
    assert p.nombre == "simulated"
    assert p.modo == "simulado"
    assert "PAGO_CULQI_LLAVE_PUBLICA" in caplog.text


def test_culqi_stub_lanza_error_con_mensaje_util(monkeypatch):
    monkeypatch.setenv("PAGO_CULQI_LLAVE_PUBLICA", "pk_test_falso")
    monkeypatch.setenv("PAGO_CULQI_SECRETA", "sk_test_falso")
    p = crear_pasarela("culqi")
    assert p.nombre == "culqi"
    with pytest.raises(PaymentGatewayError) as ei:
        p.crear_pago(PagoSolicitud(id_venta=1, monto=Decimal("1.00")))
    assert "no está activado" in str(ei.value)
