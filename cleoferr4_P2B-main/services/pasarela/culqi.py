"""Culqi: stub NO IMPLEMENTADO (Fase 2.1, Tramo 1).

Cada método lanza NotImplementedError con lo que faltaría. NADA de endpoints,
formatos, cabeceras o estados de Culqi está verificado contra su documentación:
todo lo no verificado está marcado 'pendiente de verificar'. No se inventa
ningún detalle del proveedor. Ver docs/CAMBIAR_PASARELA.md (se redacta en el
Tramo 2) antes de implementar algo aquí.

Las credenciales se guardan solo en memoria y jamás se registran en logs.
"""
from .base import Pasarela

_MENSAJE = (
    "Culqi NO está implementado todavía. Faltaría: credenciales reales, "
    "creación del checkout (endpoint y formato: pendiente de verificar en la "
    "documentación de Culqi), verificación de firma del webhook (algoritmo y "
    "cabeceras: pendiente de verificar), mapeo de estados de Culqi a "
    "EstadoPago (pendiente de verificar), reembolsos (pendiente de verificar), "
    "comisiones y límites (pendiente de verificar)."
)


class PasarelaCulqi(Pasarela):
    nombre = "culqi"

    def __init__(self, llave_publica, secreta):
        self._llave_publica = llave_publica
        self._secreta = secreta

    def crear_pago(self, solicitud):
        raise NotImplementedError(_MENSAJE)

    def verificar_pago(self, id_pago_proveedor):
        raise NotImplementedError(_MENSAJE)

    def consultar_estado(self, id_pago_proveedor):
        raise NotImplementedError(_MENSAJE)

    def procesar_webhook(self, payload, headers):
        raise NotImplementedError(_MENSAJE)
