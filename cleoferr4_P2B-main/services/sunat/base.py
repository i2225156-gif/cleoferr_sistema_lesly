"""Interfaz neutra de proveedor SUNAT (Fase 2.2).

Reglas del paquete:
- Ningún tipo ni campo propio de un proveedor concreto sale de aquí.
- EstadoComprobanteSunat mapea 1:1 con comprobante.estado_sunat; EMITIDO usa
  el valor 'enviado' que YA EXISTE en la columna (no se rompe la BD) y
  ANULADO requiere db/07 (CHECK ampliado).
- 'desconocido' no aplica aquí: los estados de comprobante son solo estos.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Optional


class EstadoComprobanteSunat(str, Enum):
    PENDIENTE = "pendiente"
    NO_APLICA = "no_aplica"
    EMITIDO = "enviado"      # valor existente en comprobante.estado_sunat
    ANULADO = "anulado"      # requiere db/07 (CHECK ampliado)


@dataclass(frozen=True)
class ConsultaDocumento:
    """Resultado neutral de consultar un RUC o DNI (sin datos de un
    proveedor concreto). 'estado': activo | no_habido | inexistente."""

    numero: str
    valido: bool
    estado: Optional[str] = None
    razon_social: Optional[str] = None
    direccion: Optional[str] = None
    ubigeo: Optional[str] = None
    simulado: bool = False


@dataclass(frozen=True)
class ComprobanteSolicitud:
    """La serie ('B001'/'F001') y el correlativo los asigna el LLAMADOR
    dentro de su transacción (helper de numeración existente); el
    proveedor los hace suyos y devuelve los artefactos."""

    id_venta: int
    tipo: str                     # 'boleta' | 'factura'
    serie: str
    numero: int
    cliente_documento: Optional[str] = None
    cliente_nombre: Optional[str] = None
    moneda: str = "PEN"
    subtotal: Optional[Decimal] = None
    igv: Optional[Decimal] = None
    total: Optional[Decimal] = None


@dataclass(frozen=True)
class ComprobanteResultado:
    id_venta: int
    serie: str
    numero: int
    estado: EstadoComprobanteSunat
    referencia_proveedor: Optional[str] = None
    hash: Optional[str] = None
    qr_contenido: Optional[str] = None
    cdr: Optional[str] = None
    fecha_emision: Optional[datetime] = None
    simulado: bool = True


class ProveedorSunat(ABC):
    nombre: ClassVar[str]

    @abstractmethod
    def consultar_ruc(self, ruc: str) -> ConsultaDocumento:
        """Consulta de RUC (respuesta neutral)."""

    @abstractmethod
    def consultar_dni(self, dni: str) -> ConsultaDocumento:
        """Consulta de DNI (respuesta neutral)."""

    @abstractmethod
    def emitir_comprobante(self, solicitud: ComprobanteSolicitud) -> ComprobanteResultado:
        """Emite (simuladamente por ahora) el comprobante de una venta pagada."""

    def consultar_estado_comprobante(self, referencia: str) -> EstadoComprobanteSunat:
        raise NotImplementedError(
            "consultar_estado_comprobante no está implementado por este proveedor."
        )

    def anular_comprobante(self, referencia: str) -> ComprobanteResultado:
        raise NotImplementedError(
            "anular_comprobante está definido en la interfaz pero SIN implementar "
            "(requiere el valor 'anulado' de db/07 y la aprobación del propietario)."
        )
