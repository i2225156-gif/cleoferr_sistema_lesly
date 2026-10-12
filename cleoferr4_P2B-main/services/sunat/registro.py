"""Fábrica de proveedores SUNAT (Fase 2.2).

SUNAT_PROVEEDOR: simulado (defecto) | apisperu (solo RUC/DNI, real) | real
(stub). Falla de forma clara si falta EMPRESA_RUC, si tiene el valor de
ejemplo de .env.example, o si SUNAT_MODO no es 'simulado'.
"""
import os

from .apisperu import ProveedorApisperu
from .base import ProveedorSunat
from .real import SunatReal
from .simulado import SunatSimulado

PROVEEDORES_VALIDOS = ("simulado", "apisperu", "real")

_RUC_EJEMPLO = "ruc-falso-de-ejemplo"


def crear_proveedor_sunat(nombre=None):
    proveedor = (nombre or os.environ.get("SUNAT_PROVEEDOR") or "simulado").strip().lower()
    if proveedor == "simulado":
        ruc = (os.environ.get("EMPRESA_RUC") or "").strip()
        if not ruc:
            raise RuntimeError(
                "SUNAT_PROVEEDOR=simulado pero falta EMPRESA_RUC en el .env "
                "(RUC del emisor de los comprobantes)."
            )
        if ruc == _RUC_EJEMPLO or ruc.startswith("ruc-falso"):
            raise RuntimeError(
                "EMPRESA_RUC tiene el valor de ejemplo de .env.example: pon el "
                "RUC real del emisor en el .env del proyecto."
            )
        modo = os.environ.get("SUNAT_MODO", "simulado")
        if modo != "simulado":
            raise RuntimeError(
                f"SUNAT_MODO inválido: '{modo}'. Único valor válido: 'simulado'."
            )
        return SunatSimulado(
            emisor_ruc=ruc,
            razon_social=os.environ.get("EMPRESA_RAZON_SOCIAL", ""),
            direccion=os.environ.get("EMPRESA_DIRECCION", ""),
            modo=modo,
        )
    if proveedor == "apisperu":
        base = os.environ.get("BASE_SUNAT")
        token = os.environ.get("TOKEN_SUNAT")
        if not base or not token:
            raise RuntimeError(
                "SUNAT_PROVEEDOR=apisperu pero faltan BASE_SUNAT y/o TOKEN_SUNAT "
                "en el .env del proyecto."
            )
        return ProveedorApisperu(base, token)
    if proveedor == "real":
        return SunatReal()
    raise ValueError(
        f"Proveedor SUNAT desconocido: '{proveedor}'. "
        f"Válidos: {', '.join(PROVEEDORES_VALIDOS)}."
    )
