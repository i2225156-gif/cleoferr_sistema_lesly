"""Paquete de pasarela de pago (Fase 2.1).

Los módulos de negocio (app, pedidos, carrito, inventario) importan SOLO
base.py (tipos neutrales e interfaz) y registro.py (fábrica). Nada de
simulada.py ni culqi.py puede importarse fuera de este paquete: lo vigila
tests/test_arquitectura.py con análisis de imports (ast).
"""
from .base import (EstadoPago, EventoWebhook, PagoResultado, PagoSolicitud,
                   Pasarela, etiqueta_estado)
from .registro import crear_pasarela
