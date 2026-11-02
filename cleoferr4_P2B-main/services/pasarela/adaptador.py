"""Adaptador que expone PaymentGateway mediante la interfaz Pasarela existente.

Permite que app.py siga usando crear_pasarela() y la interfaz Pasarela
(crear_pago, verificar_pago, consultar_estado, reembolsar, nombre, modo,
firmar_evento) mientras internamente usa el nuevo adaptador según
PAYMENT_PROVIDER.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Optional

from .base import (EstadoPago, EventoWebhook, PagoResultado, PagoSolicitud,
                   Pasarela)
from .gateway import (CargoResultado, CargoSolicitud, PaymentGateway,
                      PaymentGatewayError, crear_gateway)


def _estado_a_pasarela(estado: str) -> EstadoPago:
    return {
        "aprobado": EstadoPago.CONFIRMADO,
        "rechazado": EstadoPago.RECHAZADO,
        "pendiente": EstadoPago.PENDIENTE,
        "reembolsado": EstadoPago.REEMBOLSADO,
        "cancelado": EstadoPago.CANCELADO,
    }.get(estado, EstadoPago.DESCONOCIDO)


class PasarelaAdapter(Pasarela):
    """Envuelve una PaymentGateway y expone la interfaz Pasarela legacy."""

    def __init__(self, gateway: PaymentGateway,
                 pasarela_vieja: Optional[Pasarela] = None):
        self._gateway = gateway
        self._pasarela_vieja = pasarela_vieja
        self.nombre = gateway.nombre
        self.modo = "simulado" if gateway.nombre == "simulated" else "produccion"

    # ── Interfaz Pasarela (legacy) delegando en PaymentGateway ──────────

    def crear_pago(self, solicitud: PagoSolicitud) -> PagoResultado:
        cargo_sol = CargoSolicitud(
            id_venta=solicitud.id_venta,
            monto=solicitud.monto,
            moneda=solicitud.moneda,
            metodo=solicitud.metodo,
            clave_idempotencia=solicitud.clave_idempotencia,
            datos_cliente=solicitud.datos_cliente,
        )
        cargo = self._gateway.crear_cargo(cargo_sol)
        return PagoResultado(
            id_pago_proveedor=cargo.id,
            estado=_estado_a_pasarela(cargo.estado),
            monto=cargo.monto,
            moneda=cargo.moneda,
            checkout_url=cargo.checkout_url,
            requiere_verificacion=cargo.requiere_verificacion,
            detalle=cargo.detalle,
        )

    def verificar_pago(self, id_pago_proveedor: str) -> PagoResultado:
        cargo = self._gateway.consultar_cargo(id_pago_proveedor)
        return PagoResultado(
            id_pago_proveedor=cargo.id,
            estado=_estado_a_pasarela(cargo.estado),
            monto=cargo.monto,
            moneda=cargo.moneda,
            checkout_url=cargo.checkout_url,
            requiere_verificacion=cargo.requiere_verificacion,
            detalle=cargo.detalle,
        )

    def consultar_estado(self, id_pago_proveedor: str) -> EstadoPago:
        cargo = self._gateway.consultar_cargo(id_pago_proveedor)
        return _estado_a_pasarela(cargo.estado)

    def reembolsar(self, id_pago_proveedor: str,
                   monto: Optional[Decimal] = None) -> PagoResultado:
        cargo = self._gateway.reembolsar(id_pago_proveedor, monto)
        return PagoResultado(
            id_pago_proveedor=cargo.id,
            estado=_estado_a_pasarela(cargo.estado),
            monto=cargo.monto,
            moneda=cargo.moneda,
            checkout_url=cargo.checkout_url,
            requiere_verificacion=cargo.requiere_verificacion,
            detalle=cargo.detalle,
        )

    # ── Webhook / firma (delegado en pasarela vieja si existe) ──────────

    def procesar_webhook(self, payload: bytes,
                         headers: Mapping) -> EventoWebhook:
        if self._pasarela_vieja is not None:
            return self._pasarela_vieja.procesar_webhook(payload, headers)
        # Fallback mínimo sin firma: solo para evitar romper la ruta de webhook.
        # La firma real se verifica en la pasarela simulada subyacente.
        raise PaymentGatewayError(
            "procesar_webhook no disponible sin la pasarela simulada subyacente."
        )

    def firmar_evento(self, id_pago_proveedor: str, estado, monto,
                      tipo: str = "pago_actualizado"):
        if self._pasarela_vieja is not None:
            return self._pasarela_vieja.firmar_evento(
                id_pago_proveedor, estado, monto, tipo=tipo)
        raise PaymentGatewayError(
            "firmar_evento no disponible sin la pasarela simulada subyacente."
        )


def crear_adaptador(nombre: Optional[str] = None,
                    pasarela_vieja: Optional[Pasarela] = None) -> PasarelaAdapter:
    """Crea el adaptador según PAYMENT_PROVIDER."""
    gateway = crear_gateway(nombre)
    return PasarelaAdapter(gateway=gateway, pasarela_vieja=pasarela_vieja)
