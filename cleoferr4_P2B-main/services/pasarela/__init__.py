"""Paquete de pasarela de pago (unificado).

Expone la interfaz legacy (Pasarela, tipos neutrales), la nueva interfaz
PaymentGateway con SimulatedGateway y CulqiGateway, y la fábrica
crear_pasarela() que elige la implementación según PAYMENT_PROVIDER.

Los módulos de negocio (app, pedidos, carrito, inventario) importan SOLO
este __init__ (tipos, interfaz y fábrica). Nada de simulada.py ni
gateway.py puede importarse fuera de este paquete: lo vigila
tests/test_arquitectura.py con análisis de imports (ast).
"""
from .adaptador import PasarelaAdapter, crear_adaptador
from .base import (EstadoPago, EventoWebhook, PagoResultado, PagoSolicitud,
                   Pasarela, etiqueta_estado)
from .gateway import (CargoResultado, CargoSolicitud, CulqiGateway,
                      PaymentGateway, PaymentGatewayError, SimulatedGateway,
                      crear_gateway)
from .registro import crear_pasarela

__all__ = [
    # Interfaz legacy + tipos neutrales
    "EstadoPago",
    "EventoWebhook",
    "PagoResultado",
    "PagoSolicitud",
    "Pasarela",
    "etiqueta_estado",
    # Nueva interfaz PaymentGateway + implementaciones
    "PaymentGateway",
    "PaymentGatewayError",
    "CargoSolicitud",
    "CargoResultado",
    "SimulatedGateway",
    "CulqiGateway",
    "crear_gateway",
    # Adaptador
    "PasarelaAdapter",
    "crear_adaptador",
    # Fábrica
    "crear_pasarela",
]
