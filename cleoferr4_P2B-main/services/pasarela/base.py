"""Interfaz neutra de proveedor de pago (Fase 2.1, Tramo 1).

Reglas del paquete:
- Ningún tipo ni campo propio de un proveedor sale de services/pasarela.
- El estado final del pago lo decide el backend, nunca el navegador.
- Los estados internos (D7): pendiente | confirmado | rechazado |
  cancelado | reembolsado. 'desconocido' existe solo en memoria (estado no
  mapeable del proveedor) y jamás se persiste.
- 'cancelado' y 'reembolsado' requieren db/06 (CHECK ampliado) antes de
  poder escribirse en pago.estado; hasta entonces solo se usan los tres
  valores del CHECK actual.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Mapping, Optional


class EstadoPago(str, Enum):
    """Estados internos del pago (nomenclatura aprobada, D7)."""

    PENDIENTE = "pendiente"
    CONFIRMADO = "confirmado"
    RECHAZADO = "rechazado"
    CANCELADO = "cancelado"        # requiere db/06 antes de persistirse
    REEMBOLSADO = "reembolsado"    # requiere db/06 antes de persistirse
    DESCONOCIDO = "desconocido"    # solo en memoria: estado no mapeable


_ETIQUETAS = {
    EstadoPago.PENDIENTE: "Pendiente",
    EstadoPago.CONFIRMADO: "Aprobado",
    EstadoPago.RECHAZADO: "Rechazado",
    EstadoPago.CANCELADO: "Cancelado",
    EstadoPago.REEMBOLSADO: "Reembolsado",
    EstadoPago.DESCONOCIDO: "Desconocido",
}


def etiqueta_estado(estado):
    """Etiqueta amigable para la interfaz (D7: confirmado = 'Aprobado')."""
    return _ETIQUETAS.get(estado, estado.value.capitalize())


@dataclass(frozen=True)
class PagoSolicitud:
    """Datos neutrales para crear un pago.

    'metodo' se mapea a tipo_pago en la capa de negocio (corrección 8):
    'tarjeta' -> tarjeta | 'yape_plin' -> yape_plin | 'efectivo' -> efectivo.
    'transferencia' queda pendiente hasta existir fila en tipo_pago.
    'datos_cliente' solo lleva nombres/email: JAMÁS datos de tarjeta.
    """

    id_venta: int
    monto: Decimal
    metodo: str = "tarjeta"
    moneda: str = "PEN"
    clave_idempotencia: str = ""    # una por intento (corrección 1); nueva en reintentos (D3)
    return_url: Optional[str] = None
    descripcion: str = ""
    datos_cliente: Optional[Mapping] = None


@dataclass(frozen=True)
class PagoResultado:
    """Resultado neutral de una operación con la pasarela.

    'detalle' solo lleva metadatos neutrales (metodo, razon); sin datos
    sensibles y sin campos propios de un proveedor.
    """

    id_pago_proveedor: Optional[str]
    estado: EstadoPago
    monto: Optional[Decimal] = None
    moneda: str = "PEN"
    checkout_url: Optional[str] = None
    requiere_verificacion: bool = False
    detalle: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class EventoWebhook:
    """Evento de notificación de la pasarela, ya con la firma verificada
    (o marcada como inválida) por el propio proveedor.

    'id_evento' identifica el evento del proveedor: la idempotencia real
    (no reprocesar el mismo evento) la garantiza pago_evento con
    UNIQUE(proveedor, id_evento_proveedor) en el Tramo 2.
    """

    id_evento: str
    id_pago_proveedor: Optional[str]
    estado: Optional[EstadoPago]
    monto: Optional[Decimal]
    ocurre_en: datetime
    firma_valida: bool
    fuera_de_tiempo: bool = False
    detalle_sin_sensibles: Mapping = field(default_factory=dict)


class Pasarela(ABC):
    """Interfaz única que debe respetar todo proveedor de pago."""

    nombre: ClassVar[str]

    @abstractmethod
    def crear_pago(self, solicitud: PagoSolicitud) -> PagoResultado:
        """Crea la intención de pago y devuelve dónde continuar (checkout_url)."""

    @abstractmethod
    def verificar_pago(self, id_pago_proveedor: str) -> PagoResultado:
        """Verificación autoritativa server-to-server del estado del pago."""

    @abstractmethod
    def consultar_estado(self, id_pago_proveedor: str) -> EstadoPago:
        """Lectura ligera del estado actual (sin efectos secundarios)."""

    @abstractmethod
    def procesar_webhook(self, payload: bytes, headers: Mapping) -> EventoWebhook:
        """Interpreta y VERIFICA LA FIRMA de una notificación entrante."""

    def reembolsar(self, id_pago_proveedor: str,
                   monto: Optional[Decimal] = None) -> PagoResultado:
        """Definido en la interfaz pero SIN implementar (decisión D4)."""
        raise NotImplementedError(
            "reembolsar() está definido en la interfaz pero sin implementar "
            "(decisión D4: requiere db/06 y tu aprobación)."
        )
