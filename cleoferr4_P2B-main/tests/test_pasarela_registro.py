"""Tramo 1: fábrica crear_pasarela (valor inválido y credenciales faltantes)."""
from decimal import Decimal

import pytest

from services.pasarela import registro
from services.pasarela.base import PagoSolicitud
from services.pasarela.registro import crear_pasarela


def test_proveedor_desconocido_lanza_valueerror():
    with pytest.raises(ValueError) as ei:
        crear_pasarela("stripe")
    assert "simulada, culqi" in str(ei.value)


def test_simulada_sin_secreto_lanza_runtimeerror(monkeypatch):
    monkeypatch.setenv("PAGO_PROVEEDOR", "simulada")
    monkeypatch.delenv("PAGO_SIMULADA_WEBHOOK_SECRETO", raising=False)
    with pytest.raises(RuntimeError):
        crear_pasarela()


def test_simulada_con_secreto_de_ejemplo_lanza_runtimeerror(monkeypatch):
    monkeypatch.setenv("PAGO_PROVEEDOR", "simulada")
    monkeypatch.setenv("PAGO_SIMULADA_WEBHOOK_SECRETO", registro._SECRETO_EJEMPLO)
    with pytest.raises(RuntimeError):
        crear_pasarela()


def test_simulada_ok(monkeypatch):
    monkeypatch.setenv("PAGO_PROVEEDOR", "simulada")
    monkeypatch.setenv("PAGO_SIMULADA_WEBHOOK_SECRETO", "secreto-real-solo-para-test")
    monkeypatch.setenv("PAGO_WEBHOOK_TOLERANCIA_SEG", "300")
    p = crear_pasarela()
    assert p.nombre == "simulada"
    assert p.modo == "simulado"


def test_culqi_sin_credenciales_lanza_runtimeerror(monkeypatch):
    monkeypatch.delenv("PAGO_CULQI_LLAVE_PUBLICA", raising=False)
    monkeypatch.delenv("PAGO_CULQI_SECRETA", raising=False)
    with pytest.raises(RuntimeError) as ei:
        crear_pasarela("culqi")
    assert "PAGO_CULQI_LLAVE_PUBLICA" in str(ei.value)


def test_culqi_stub_lanza_not_implemented_con_mensaje_util(monkeypatch):
    monkeypatch.setenv("PAGO_CULQI_LLAVE_PUBLICA", "pk_test_falso")
    monkeypatch.setenv("PAGO_CULQI_SECRETA", "sk_test_falso")
    p = crear_pasarela("culqi")
    with pytest.raises(NotImplementedError) as ei:
        p.crear_pago(PagoSolicitud(id_venta=1, monto=Decimal("1.00")))
    assert "pendiente de verificar" in str(ei.value)
