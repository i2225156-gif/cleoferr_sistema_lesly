"""Paquete de adaptador de pasarela de pago.

Contiene la interfaz PaymentGateway, las implementaciones SimulatedGateway
(activa por defecto) y CulqiGateway (estructura sin activar), y la fábrica
crear_gateway() que elige la implementación según PAYMENT_PROVIDER.
"""
from .gateway import (
    PaymentGateway,
    PaymentGatewayError,
    CargoSolicitud,
    CargoResultado,
    SimulatedGateway,
    CulqiGateway,
    crear_gateway,
)
from .adaptador import PasarelaAdapter

__all__ = [
    "PaymentGateway",
    "PaymentGatewayError",
    "CargoSolicitud",
    "CargoResultado",
    "SimulatedGateway",
    "CulqiGateway",
    "crear_gateway",
    "PasarelaAdapter",
]
