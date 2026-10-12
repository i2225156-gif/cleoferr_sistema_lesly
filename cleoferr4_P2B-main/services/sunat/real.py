"""SUNAT real: stub NO IMPLEMENTADO (Fase 2.2).

SUNAT no ofrece un único endpoint sencillo para emitir comprobantes: en la
práctica se emite mediante un PSE/OSE o un proveedor de facturación
electrónica, o enviando XML firmado. NADA de formatos, endpoints ni
esquemas está verificado aquí: todo queda 'pendiente de verificar' hasta
que el propietario elija el proveedor. Este stub no inventa nada.
"""
from .base import ProveedorSunat

_MENSAJE = (
    "SUNAT real NO está implementado. Faltaría: elegir PSE/OSE o proveedor "
    "de facturación electrónica, esquema del comprobante electrónico "
    "(pendiente de verificar en la documentación oficial vigente), firma "
    "del XML y certificado (pendiente de verificar), envío a SUNAT "
    "(pendiente de verificar), consulta de estado y CDR real "
    "(pendiente de verificar)."
)


class SunatReal(ProveedorSunat):
    nombre = "real"

    def consultar_ruc(self, ruc):
        raise NotImplementedError(_MENSAJE)

    def consultar_dni(self, dni):
        raise NotImplementedError(_MENSAJE)

    def emitir_comprobante(self, solicitud):
        raise NotImplementedError(_MENSAJE)
