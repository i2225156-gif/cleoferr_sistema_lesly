"""Fábrica de pasarelas (Parte 1).

Elige el proveedor según PAYMENT_PROVIDER (valor por defecto: "simulated")
y devuelve un PasarelaAdapter que expone la interfaz Pasarela legacy
delegando internamente en la nueva PaymentGateway.

Si se elige "culqi" sin llaves, lanza PaymentGatewayError con un mensaje
claro (no un error 500).
"""
from __future__ import annotations

import os

from ..pasarela.base import Pasarela
from ..pasarela.simulada import PasarelaSimulada
from ..payment import crear_adaptador, PaymentGatewayError

# Valor publicado en .env.example: si alguien arranca con esto, es un error.
_SECRETO_EJEMPLO = ("cambia-este-secreto-genera-uno-nuevo-con-python-c-import"
                    "-secrets-print-secrets-token-hex-32")

PROVEEDORES_VALIDOS = ("simulated", "culqi")


def crear_pasarela(nombre: Optional[str] = None) -> Pasarela:
    """Devuelve el adaptador de pasarela según PAYMENT_PROVIDER.

    Mantiene compatibilidad con la variable PAGO_PROVEEDOR por si algún
    entorno aún la usa, pero PAYMENT_PROVIDER tiene prioridad.
    """
    from typing import Optional  # noqa: F401  (mantenido por claridad)
    proveedor = (
        nombre
        or os.environ.get("PAYMENT_PROVIDER")
        or os.environ.get("PAGO_PROVEEDOR")
        or "simulated"
    ).strip().lower()

    if proveedor == "simulated":
        secreto = os.environ.get("PAGO_SIMULADA_WEBHOOK_SECRETO", "") or \
                  os.environ.get("PAYMENT_SIMULATED_WEBHOOK_SECRET", "")
        if not secreto or secreto == _SECRETO_EJEMPLO or secreto.startswith("cambia-este"):
            raise PaymentGatewayError(
                "PAGO_PROVEEDOR/PAYMENT_PROVIDER=simulated pero falta el secreto "
                "del webhook (PAGO_SIMULADA_WEBHOOK_SECRETO o "
                "PAYMENT_SIMULATED_WEBHOOK_SECRET) en el .env del proyecto, o "
                "tiene el valor de ejemplo. Genera uno con: "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        tolerancia = int(os.environ.get("PAGO_WEBHOOK_TOLERANCIA_SEG", "300"))
        modo = os.environ.get("PAGO_MODO", "simulado")
        if modo != "simulado":
            raise PaymentGatewayError(
                f"PAGO_MODO inválido: '{modo}'. Único valor válido: 'simulado'."
            )
        pasarela_vieja = PasarelaSimulada(
            secreto_webhook=secreto, tolerancia_seg=tolerancia, modo=modo)
        return crear_adaptador("simulated", pasarela_vieja=pasarela_vieja)

    if proveedor == "culqi":
        llave_publica = os.environ.get("PAGO_CULQI_LLAVE_PUBLICA", "")
        llave_secreta = os.environ.get("PAGO_CULQI_SECRETA", "")
        if not llave_publica or not llave_secreta:
            raise PaymentGatewayError(
                "PAYMENT_PROVIDER=culqi pero faltan credenciales de Culqi en "
                "el .env: PAGO_CULQI_LLAVE_PUBLICA y PAGO_CULQI_SECRETA. "
                "La integración real de Culqi se hará en otro ciclo "
                "(Culqi exige RUC para entregar llaves)."
            )
        from ..payment import CulqiGateway
        gateway = CulqiGateway(llave_publica=llave_publica,
                               llave_secreta=llave_secreta)
        return crear_adaptador("culqi", pasarela_vieja=None)

    raise PaymentGatewayError(
        f"Proveedor de pago desconocido: '{proveedor}'. "
        f"Valores válidos: {', '.join(PROVEEDORES_VALIDOS)}."
    )
