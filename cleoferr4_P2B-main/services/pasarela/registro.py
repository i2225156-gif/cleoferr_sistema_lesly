"""Fábrica de pasarelas (unificada en services/pasarela).

Elige el proveedor según PAYMENT_PROVIDER (valor por defecto: "simulated")
y devuelve un PasarelaAdapter que expone la interfaz Pasarela legacy
delegando internamente en la nueva PaymentGateway.

Si se elige "culqi" sin llaves, usa "simulated" con advertencia en el log
(no falla). Toda la configuración se lee de configuracion_pago(), que da
prioridad al nombre en español y usa el inglés como fallback.
"""
from __future__ import annotations

from typing import Optional

from .adaptador import crear_adaptador
from .base import Pasarela
from .config import configuracion_pago
from .gateway import CulqiGateway, PaymentGatewayError
from .simulada import PasarelaSimulada

# Valor publicado en .env.example: si alguien arranca con esto, es un error.
_SECRETO_EJEMPLO = ("cambia-este-secreto-genera-uno-nuevo-con-python-c-import"
                    "-secrets-print-secrets-token-hex-32")

PROVEEDORES_VALIDOS = ("simulated", "culqi")


def crear_pasarela(nombre: Optional[str] = None) -> Pasarela:
    """Devuelve el adaptador de pasarela según PAYMENT_PROVIDER.

    Mantiene compatibilidad con PAGO_PROVEEDOR y PAYMENT_PROVIDER.
    La configuración se lee única y exclusivamente desde configuracion_pago().
    """
    cfg = configuracion_pago()

    proveedor = nombre or cfg["proveedor"]

    if proveedor == "simulated":
        secreto = cfg["secreto_webhook"]
        if (not secreto or secreto == _SECRETO_EJEMPLO
                or secreto.startswith("cambia-este")):
            raise PaymentGatewayError(
                "Falta el secreto "
                "del webhook (PAGO_SIMULADA_WEBHOOK_SECRETO o "
                "PAYMENT_SIMULATED_WEBHOOK_SECRET) en el .env del proyecto, o "
                "tiene el valor de ejemplo. Genera uno con: "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        tolerancia = cfg["tolerancia_seg"]
        modo = cfg["modo"]
        if modo != "simulado":
            raise PaymentGatewayError(
                "PAGO_MODO/PAYMENT_MODO debe ser 'simulado'."
            )
        pasarela_vieja = PasarelaSimulada(
            secreto_webhook=secreto, tolerancia_seg=tolerancia, modo=modo)
        return crear_adaptador("simulated", pasarela_vieja=pasarela_vieja)

    if proveedor == "culqi":
        llave_publica = cfg["culqi_llave_publica"]
        llave_secreta = cfg["culqi_secreta"]
        if not llave_publica or not llave_secreta:
            # No falla: usa simulated con advertencia.
            import logging
            logging.getLogger(__name__).warning(
                "Faltan credenciales de Culqi "
                "(PAYMENT_PROVIDER/PAGO_PROVEEDOR): se usa la pasarela "
                "simulada. Configura PAGO_CULQI_LLAVE_PUBLICA y "
                "PAGO_CULQI_SECRETA en .env.")
            pasarela_vieja = PasarelaSimulada(
                secreto_webhook=cfg["secreto_webhook"],
                tolerancia_seg=cfg["tolerancia_seg"],
                modo=cfg["modo"],
            )
            return crear_adaptador(
                "simulated",
                pasarela_vieja=pasarela_vieja,
            )
        gateway = CulqiGateway(llave_publica=llave_publica,
                               llave_secreta=llave_secreta)
        return crear_adaptador("culqi", pasarela_vieja=None)

    raise PaymentGatewayError(
        "Proveedor de pago desconocido. Revisa PAYMENT_PROVIDER/PAGO_PROVEEDOR. "
        f"Valores válidos: {', '.join(PROVEEDORES_VALIDOS)}."
    )
