"""Fábrica de pasarelas (Fase 2.1, Tramo 1).

Elige el proveedor según PAGO_PROVEEDOR y falla de forma clara si el valor
es desconocido o faltan credenciales. El secreto del webhook de la simulada
es obligatorio y no puede ser el valor de ejemplo de .env.example
(corrección 4): la app debe fallar al arrancar en ese caso.
"""
import os

from .base import Pasarela
from .culqi import PasarelaCulqi
from .simulada import PasarelaSimulada

PROVEEDORES_VALIDOS = ("simulada", "culqi")

# Valor publicado en .env.example: si alguien arranca con esto, es un error.
_SECRETO_EJEMPLO = ("cambia-este-secreto-genera-uno-nuevo-con-python-c-import"
                    "-secrets-print-secrets-token-hex-32")


def crear_pasarela(nombre=None):
    """Devuelve la Pasarela del proveedor indicado (o PAGO_PROVEEDOR)."""
    proveedor = (nombre or os.environ.get("PAGO_PROVEEDOR") or "simulada").strip().lower()
    if proveedor == "simulada":
        secreto = os.environ.get("PAGO_SIMULADA_WEBHOOK_SECRETO", "")
        if not secreto:
            raise RuntimeError(
                "PAGO_PROVEEDOR=simulada pero falta PAGO_SIMULADA_WEBHOOK_SECRETO "
                "en el .env del proyecto."
            )
        if secreto == _SECRETO_EJEMPLO or secreto.startswith("cambia-este"):
            raise RuntimeError(
                "PAGO_SIMULADA_WEBHOOK_SECRETO tiene el valor de ejemplo de "
                ".env.example: genera un secreto real "
                "(python -c \"import secrets; print(secrets.token_hex(32))\")."
            )
        tolerancia = int(os.environ.get("PAGO_WEBHOOK_TOLERANCIA_SEG", "300"))
        modo = os.environ.get("PAGO_MODO", "simulado")
        if modo != "simulado":
            raise RuntimeError(
                f"PAGO_MODO inválido: '{modo}'. Único valor válido: 'simulado' "
                "(corrígelo en el .env del proyecto)."
            )
        return PasarelaSimulada(secreto_webhook=secreto,
                                tolerancia_seg=tolerancia, modo=modo)
    if proveedor == "culqi":
        faltan = [v for v in ("PAGO_CULQI_LLAVE_PUBLICA", "PAGO_CULQI_SECRETA")
                  if not os.environ.get(v)]
        if faltan:
            raise RuntimeError(
                "PAGO_PROVEEDOR=culqi pero faltan credenciales en el .env del "
                f"proyecto: {', '.join(faltan)}."
            )
        return PasarelaCulqi(llave_publica=os.environ["PAGO_CULQI_LLAVE_PUBLICA"],
                            secreta=os.environ["PAGO_CULQI_SECRETA"])
    raise ValueError(
        f"Proveedor de pago desconocido: '{proveedor}'. "
        f"Valores válidos: {', '.join(PROVEEDORES_VALIDOS)}."
    )
