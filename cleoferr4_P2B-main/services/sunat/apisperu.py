"""Proveedor 'apisperu': envoltura REAL de la API Perú SOLO para RUC/DNI.

Decisión D5: se usa únicamente para CONSULTAR documentos; la emisión de
comprobantes NUNCA va por aquí (va por el proveedor de facturación:
simulado hoy, PSE/OSE en el futuro). El token NUNCA se imprime ni se
registra en logs; solo viaja al servidor de la API.
"""
import json
import logging
import urllib.error
import urllib.request

from .base import ConsultaDocumento, ProveedorSunat

logger = logging.getLogger(__name__)


class ProveedorApisperu(ProveedorSunat):
    nombre = "apisperu"

    def __init__(self, base_url, token, timeout=10):
        self._base = (base_url or "").rstrip("/")
        self._token = token
        self._timeout = timeout

    def _consultar(self, tipo, numero):
        if not self._token:
            return ConsultaDocumento(numero=numero, valido=False,
                                      estado="inexistente", simulado=False)
        url = f"{self._base}/{tipo}/{numero}?token={self._token}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning("API de documentos respondió %s para %s (sin datos sensibles)",
                           e.code, tipo)
            return ConsultaDocumento(numero=numero, valido=False,
                                      estado="inexistente", simulado=False)
        except Exception:
            logger.exception("Error consultando la API de documentos")
            return ConsultaDocumento(numero=numero, valido=False,
                                     estado="inexistente", simulado=False)
        if not isinstance(datos, dict):
            return ConsultaDocumento(numero=numero, valido=False,
                                     estado="inexistente", simulado=False)
        razon = datos.get("razonSocial") or datos.get("razon_social") or datos.get("nombre")
        valido = bool(razon)
        return ConsultaDocumento(
            numero=numero, valido=valido,
            estado="activo" if valido else "inexistente",
            razon_social=razon, direccion=datos.get("direccion"),
            ubigeo=datos.get("ubigeo"), simulado=False)

    def consultar_ruc(self, ruc):
        return self._consultar("ruc", (ruc or "").strip())

    def consultar_dni(self, dni):
        return self._consultar("dni", (dni or "").strip())

    def emitir_comprobante(self, solicitud):
        raise NotImplementedError(
            "API Perú solo se usa para consultar RUC/DNI (decisión D5); la "
            "emisión va por el proveedor de facturación (simulado/real)."
        )
