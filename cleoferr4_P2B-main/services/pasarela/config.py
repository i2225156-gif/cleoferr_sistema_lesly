"""Configuración centralizada de pasarela de pago.

Todas las variables de entorno se leen aquí, con prioridad del nombre en
español (el que ya existe en el .env del proyecto) y fallback al nombre en
inglés. Ningún módulo de pago lee os.environ directamente fuera de esta
función.
"""
from __future__ import annotations

import os
from typing import Optional


def _env(es: str, en: str, default: Optional[str] = None) -> Optional[str]:
    """Lee primero la variable en español, luego la en inglés, luego default."""
    return os.environ.get(es) or os.environ.get(en) or default


def _env_int(es: str, en: str, default: str) -> int:
    """Lee un entero sin exponer el valor recibido en el mensaje."""
    try:
        return int(str(_env(es, en, default)).strip())
    except (TypeError, ValueError):
        raise ValueError(
            "%s/%s debe ser un número entero" % (es, en)
        ) from None


def _env_bool(es: str, en: str, default: bool = False) -> bool:
    valor = _env(es, en)
    if valor is None:
        return default
    return str(valor).strip().lower() in ("1", "true", "sí", "si", "yes", "on")


def configuracion_pago() -> dict:
    """Devuelve un diccionario con toda la configuración de pago.

    Claves:
      - proveedor: 'simulated' o 'culqi'
      - secreto_webhook: secreto para firmar/verificar webhooks (simulado)
      - tolerancia_seg: tolerancia de tiempo para webhooks (segundos)
      - modo: modo de operación ('simulado' | 'produccion')
      - culqi_llave_publica: llave pública de Culqi
      - culqi_secreta: llave secreta de Culqi
      - expiracion_min: minutos antes de expirar un pedido pendiente
      - sunat_api_base: URL base de la API Perú/SUNAT
      - sunat_api_token: token de autenticación de la API Perú/SUNAT
    """
    proveedor_raw = _env("PAYMENT_PROVIDER", "PAGO_PROVEEDOR", "simulated")
    proveedor = _normalizar_proveedor(proveedor_raw)

    return {
        "proveedor": proveedor,
        "secreto_webhook": _env(
            "PAYMENT_SIMULATED_WEBHOOK_SECRET",
            "PAGO_SIMULADA_WEBHOOK_SECRETO",
            "",
        ),
        "tolerancia_seg": _env_int(
            "PAYMENT_WEBHOOK_TOLERANCIA_SEG",
            "PAGO_WEBHOOK_TOLERANCIA_SEG",
            "300",
        ),
        "modo": _env("PAYMENT_MODO", "PAGO_MODO", "simulado"),
        "culqi_llave_publica": _env(
            "PAYMENT_CULQI_LLAVE_PUBLICA",
            "PAGO_CULQI_LLAVE_PUBLICA",
            "",
        ),
        "culqi_secreta": _env(
            "PAYMENT_CULQI_SECRETA",
            "PAGO_CULQI_SECRETA",
            "",
        ),
        "expiracion_min": _env_int(
            "PAGO_EXPIRACION_MIN", "PAYMENT_EXPIRATION_MIN", "30"
        ),
        "sunat_api_base": _env(
            "BASE_SUNAT",
            "SUNAT_API_BASE",
            "https://dniruc.apisperu.com/api/v1",
        ),
        "sunat_api_token": _env(
            "TOKEN_SUNAT",
            "PAYMENT_SUNAT_TOKEN",
            "",
        ),
    }


def _normalizar_proveedor(valor: Optional[str]) -> str:
    """Normaliza el nombre del proveedor de pago."""
    if valor is None:
        return "simulated"
    v = str(valor).strip().lower()
    if v in ("", "simulada", "simulado", "simulated", "simulación"):
        return "simulated"
    if v == "culqi":
        return "culqi"
    return "simulated"


__all__ = ["configuracion_pago"]
