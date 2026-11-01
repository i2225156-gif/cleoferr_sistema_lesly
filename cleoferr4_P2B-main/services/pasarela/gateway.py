"""Interfaz y adaptadores de pasarela de pago (unificado en services/pasarela).

Define PaymentGateway con crear_cargo, consultar_cargo y reembolsar, y dos
implementaciones:

- SimulatedGateway: pasarela de simulación (activa por defecto). Soporta
  tarjeta, Yape, Plin y transferencia bancaria. Nunca guarda el número
  completo de tarjeta ni el CVV.
- CulqiGateway: estructura lista para el API real de Culqi (llaves pk/sk
  desde .env, monto en céntimos, PEN), sin activar. Todos sus métodos
  lanzan PaymentGatewayError con un mensaje claro.

La implementación se elige con la variable de entorno PAYMENT_PROVIDER
("simulated" por defecto). Si se elige "culqi" sin llaves, se lanza un
error claro, nunca un 500.
"""
from __future__ import annotations

import os
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .config import configuracion_pago


class PaymentGatewayError(Exception):
    """Error controlado de la pasarela: se muestra al cliente, nunca 500."""


@dataclass(frozen=True)
class CargoSolicitud:
    """Datos neutrales para crear un cargo.

    'monto' viaja en unidades monetarias (S/), ej. Decimal("12.50").
    'metodo': tarjeta | yape | plin | transferencia
    'datos_cliente' solo lleva nombres/email: JAMÁS datos de tarjeta.
    """

    id_venta: int
    monto: Decimal
    moneda: str = "PEN"
    metodo: str = "tarjeta"
    clave_idempotencia: str = ""
    datos_cliente: Optional[dict] = None


@dataclass(frozen=True)
class CargoResultado:
    """Resultado de una operación con la pasarela.

    'id' tiene forma similar al API de Culqi: "chr_sim_xxx".
    Solo viajan los últimos 4 dígitos y la marca de la tarjeta, nunca el
    número completo ni el CVV.
    """

    id: str
    estado: str                    # aprobado | rechazado | pendiente | reembolsado
    monto: Decimal
    moneda: str = "PEN"
    ultimos_4: Optional[str] = None
    marca: Optional[str] = None
    mensaje: str = ""
    requiere_verificacion: bool = False
    checkout_url: Optional[str] = None
    detalle: dict = field(default_factory=dict)


class PaymentGateway(ABC):
    """Interfaz única que debe respetar todo proveedor de pago."""

    nombre: str = "base"

    @abstractmethod
    def crear_cargo(self, solicitud: CargoSolicitud) -> CargoResultado:
        """Crea la intención de pago y devuelve dónde continuar (checkout_url)."""

    @abstractmethod
    def consultar_cargo(self, id_cargo: str) -> CargoResultado:
        """Lectura ligera del estado actual (sin efectos secundarios)."""

    @abstractmethod
    def reembolsar(self, id_cargo: str,
                   monto: Optional[Decimal] = None) -> CargoResultado:
        """Reembolsa un cargo (total o parcial)."""


# ---------------------------------------------------------------------------
# SimulatedGateway (activa por defecto)
# ---------------------------------------------------------------------------

_TARJETA_APRUEBA = "4111111111111111"
_TARJETA_RECHAZA_FONDOS = "4000000000000002"


class SimulatedGateway(PaymentGateway):
    """Pasarela de simulación para la demo.

    Reglas:
    - Determinista: mismas entradas -> mismos resultados.
    - Tarjeta 4111 1111 1111 1111 -> aprobada.
    - Tarjeta 4000 0000 0000 0002 -> rechazada por fondos insuficientes.
    - Cualquier otra tarjeta -> rechazada como inválida.
    - Yape y Plin: aprobados (se asume código de operación de 6 dígitos).
    - Transferencia: queda 'pendiente' hasta que la administradora la confirme.
    - Nunca guarda número completo de tarjeta ni CVV.
    """

    nombre = "simulated"

    def __init__(self, secreto_webhook: str = "", tolerancia_seg: int = 300):
        self._secreto = secreto_webhook
        self._tolerancia_seg = tolerancia_seg
        self._cargos: dict[str, dict] = {}

    def crear_cargo(self, solicitud: CargoSolicitud) -> CargoResultado:
        clave = solicitud.clave_idempotencia or f"venta-{solicitud.id_venta}"
        # Sin cobros duplicados: mismo intento devuelve el cargo existente.
        for existente in self._cargos.values():
            if existente["clave_idempotencia"] == clave:
                return self._resultado(existente)

        id_cargo = f"chr_sim_{secrets.token_hex(8)}"
        metodo = (solicitud.metodo or "tarjeta").strip().lower()
        estado = "pendiente"

        cargo = {
            "id": id_cargo,
            "clave_idempotencia": clave,
            "id_venta": solicitud.id_venta,
            "monto": solicitud.monto,
            "moneda": solicitud.moneda,
            "metodo": metodo,
            "estado": estado,
            "mensaje": self._mensaje_estado(estado, metodo),
            "ultimos_4": None,
            "marca": None,
            "requiere_verificacion": metodo != "tarjeta",
            "creado_en": time.time(),
        }
        # Solo tarjeta guarda últimos 4 y marca (siempre enmascarados).
        if metodo == "tarjeta":
            numero = "".join(
                ch for ch in str((solicitud.datos_cliente or {}).get("numero_tarjeta", ""))
                if ch.isdigit()
            )
            if len(numero) >= 4:
                cargo["ultimos_4"] = numero[-4:]
                cargo["marca"] = "simulada"

        self._cargos[id_cargo] = cargo
        return self._resultado(cargo)

    def consultar_cargo(self, id_cargo: str) -> CargoResultado:
        cargo = self._cargos.get(id_cargo)
        if not cargo:
            raise PaymentGatewayError(f"Cargo no encontrado: {id_cargo}")
        return self._resultado(cargo)

    def reembolsar(self, id_cargo: str,
                   monto: Optional[Decimal] = None) -> CargoResultado:
        cargo = self._cargos.get(id_cargo)
        if not cargo:
            raise PaymentGatewayError(f"Cargo no encontrado: {id_cargo}")
        if cargo["estado"] != "aprobado":
            raise PaymentGatewayError(
                "Solo se puede reembolsar un cargo aprobado."
            )
        cargo["estado"] = "reembolsado"
        cargo["monto_reembolso"] = monto or cargo["monto"]
        return self._resultado(cargo)

    # ── internos ──────────────────────────────────────────────────────

    def _evaluar_estado(self, metodo: str, datos_cliente: dict) -> str:
        if metodo in ("yape", "plin", "transferencia"):
            return "pendiente"  # pendiente de verificación por la administradora
        # tarjeta (default): decisión determinista por número de prueba
        numero = "".join(
            ch for ch in str(datos_cliente.get("numero_tarjeta", "")) if ch.isdigit()
        )
        if numero == _TARJETA_APRUEBA:
            return "aprobado"
        if numero == _TARJETA_RECHAZA_FONDOS:
            return "rechazado"
        return "rechazado"

    def _mensaje_estado(self, estado: str, metodo: str) -> str:
        if metodo in ("yape", "plin"):
            if estado == "pendiente":
                return (
                    f"Pago vía {metodo.upper()} recibido (simulado). "
                    "Pendiente de verificación por la administradora."
                )
            return f"Pago vía {metodo.upper()} procesado."
        if metodo == "transferencia":
            return (
                "Transferencia bancaria recibida. El pedido queda pendiente "
                "de verificación por la administradora."
            ) if estado == "pendiente" else "Transferencia procesada."
        if estado == "pendiente":
            return "Pago iniciado. Continúa en la pantalla de pago (simulación)."
        if estado == "aprobado":
            return "Pago aprobado (simulación)."
        if estado == "rechazado":
            return "Pago rechazado (simulación). Intenta con otro método."
        return "Estado de pago desconocido."

    def _resultado(self, cargo: dict) -> CargoResultado:
        return CargoResultado(
            id=cargo["id"],
            estado=cargo["estado"],
            monto=cargo["monto"],
            moneda=cargo.get("moneda", "PEN"),
            ultimos_4=cargo.get("ultimos_4"),
            marca=cargo.get("marca"),
            mensaje=cargo.get("mensaje", ""),
            requiere_verificacion=cargo.get("requiere_verificacion", False),
            checkout_url=f"/pago/simulado/{cargo['id']}",
            detalle={
                "metodo": cargo.get("metodo"),
                "razon": cargo.get("razon", ""),
            },
        )


# ---------------------------------------------------------------------------
# CulqiGateway (estructura sin activar)
# ---------------------------------------------------------------------------

class CulqiGateway(PaymentGateway):
    """Estructura lista para el API real de Culqi, sin activar.

    Culqi exige RUC para entregar llaves, por lo que la integración real
    se hará en otro ciclo. Esta clase solo define la estructura: lee las
    llaves desde .env y prepara el monto en céntimos con moneda PEN.
    """

    nombre = "culqi"

    def __init__(self, llave_publica: str, llave_secreta: str):
        if not llave_publica or not llave_secreta:
            raise PaymentGatewayError(
                "Culqi requiere PAGO_CULQI_LLAVE_PUBLICA y PAGO_CULQI_SECRETA "
                "en el archivo .env."
            )
        self._llave_publica = llave_publica
        self._llave_secreta = llave_secreta

    def _monto_en_centimos(self, monto: Decimal) -> int:
        """Culqi espera el monto en céntimos (ej. S/ 12.50 -> 1250)."""
        return int((monto * 100).quantize(Decimal("1")))

    def crear_cargo(self, solicitud: CargoSolicitud) -> CargoResultado:
        raise PaymentGatewayError(
            "Culqi no está activado todavía. La integración real se hará "
            "en otro ciclo (Culqi exige RUC para entregar llaves)."
        )

    def consultar_cargo(self, id_cargo: str) -> CargoResultado:
        raise PaymentGatewayError(
            "Culqi no está activado todavía. La integración real se hará "
            "en otro ciclo."
        )

    def reembolsar(self, id_cargo: str,
                   monto: Optional[Decimal] = None) -> CargoResultado:
        raise PaymentGatewayError(
            "Culqi no está activado todavía. La integración real se hará "
            "en otro ciclo."
        )


# ---------------------------------------------------------------------------
# Fábrica
# ---------------------------------------------------------------------------

_PROVEEDORES_VALIDOS = ("simulated", "culqi")
_SECRETO_EJEMPLO = ("cambia-este-secreto-genera-uno-nuevo-con-python-c-import"
                    "-secrets-print-secrets-token-hex-32")


def crear_gateway(nombre: Optional[str] = None) -> PaymentGateway:
    """Devuelve la PaymentGateway del proveedor indicado (o PAYMENT_PROVIDER).

    Valor por defecto: "simulated".
    Si se elige "culqi" sin llaves, lanza PaymentGatewayError con mensaje
    claro (no un error 500).
    """
    cfg = configuracion_pago()
    proveedor = (nombre or cfg["proveedor"] or "simulated").strip().lower()

    if proveedor == "simulated":
        secreto = (os.environ.get("PAYMENT_SIMULATED_WEBHOOK_SECRET") or os.environ.get("PAGO_SIMULADA_WEBHOOK_SECRETO") or "").strip()
        if not secreto or secreto == _SECRETO_EJEMPLO or secreto.startswith("cambia-este"):
            raise PaymentGatewayError(
                "Falta PAYMENT_SIMULATED_WEBHOOK_SECRET/"
                "PAGO_SIMULADA_WEBHOOK_SECRETO en el .env del proyecto "
                "(o tiene el valor de ejemplo). Genera uno con: "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return SimulatedGateway(
            secreto_webhook=secreto,
            tolerancia_seg=int(str(cfg["tolerancia_seg"]).strip()),
        )

    if proveedor == "culqi":
        llave_publica = os.environ.get("PAGO_CULQI_LLAVE_PUBLICA", "")
        llave_secreta = os.environ.get("PAGO_CULQI_SECRETA", "")
        if not llave_publica or not llave_secreta:
            raise PaymentGatewayError(
                "Faltan credenciales de Culqi en el .env: "
                "PAGO_CULQI_LLAVE_PUBLICA y PAGO_CULQI_SECRETA. "
                "La integración real de Culqi se hará en otro ciclo."
            )
        return CulqiGateway(llave_publica=llave_publica, llave_secreta=llave_secreta)

    raise PaymentGatewayError(
        "Proveedor de pago desconocido. Revisa PAYMENT_PROVIDER/PAGO_PROVEEDOR. "
        f"Valores válidos: {', '.join(_PROVEEDORES_VALIDOS)}."
    )
