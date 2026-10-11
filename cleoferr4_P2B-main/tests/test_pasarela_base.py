"""Tramo 1: tipos neutrales e interfaz de services/pasarela/base.py."""
import dataclasses
from decimal import Decimal

import pytest

from services.pasarela.base import (EstadoPago, EventoWebhook, PagoResultado,
                                    PagoSolicitud, Pasarela, etiqueta_estado)


def test_estado_pago_nomenclatura_aprobada_d7():
    assert [e.value for e in EstadoPago] == [
        "pendiente", "confirmado", "rechazado", "cancelado", "reembolsado", "desconocido",
    ]


def test_etiquetas_amigables_d7():
    assert etiqueta_estado(EstadoPago.CONFIRMADO) == "Aprobado"
    assert etiqueta_estado(EstadoPago.PENDIENTE) == "Pendiente"
    assert etiqueta_estado(EstadoPago.RECHAZADO) == "Rechazado"
    assert etiqueta_estado(EstadoPago.CANCELADO) == "Cancelado"
    assert etiqueta_estado(EstadoPago.REEMBOLSADO) == "Reembolsado"


def test_tipos_neutrales_son_inmutables():
    s = PagoSolicitud(id_venta=1, monto=Decimal("10.50"))
    r = PagoResultado(id_pago_proveedor="x", estado=EstadoPago.PENDIENTE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.monto = Decimal("1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.estado = EstadoPago.CONFIRMADO


def test_pasarela_es_abstracta():
    with pytest.raises(TypeError):
        Pasarela()


def test_reembolsar_definido_pero_sin_implementar_d4():
    class _Min(Pasarela):
        nombre = "min"

        def crear_pago(self, solicitud): ...
        def verificar_pago(self, id_pago_proveedor): ...
        def consultar_estado(self, id_pago_proveedor): ...
        def procesar_webhook(self, payload, headers): ...

    with pytest.raises(NotImplementedError):
        _Min().reembolsar("x")
