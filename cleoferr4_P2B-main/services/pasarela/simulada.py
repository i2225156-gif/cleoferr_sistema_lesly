"""Implementación SIMULADA de la pasarela para la demo (Fase 2.1, Tramo 1).

Reglas:
- Determinista: mismas entradas -> mismos resultados (tarjetas de prueba).
- Los datos de tarjeta se evalúan y se descartan: nunca salen del módulo
  ni se guardan (regla A.3).
- Firma sus propios eventos de webhook con HMAC-SHA256, igual que un
  proveedor real, para que /webhooks/pago/simulada (Tramo 2) se ejercite
  con el mismo mecanismo de verificación.
- El estado interno que fija procesar_checkout() NO confirma pedidos: la
  confirmación real llega por webhook o verificar_pago y la ejecuta
  confirmar_pago en el backend (regla A.4).
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from .base import (EstadoPago, EventoWebhook, PagoResultado, PagoSolicitud,
                   Pasarela)

TARJETA_APROBADA = "4111111111111111"
TARJETA_RECHAZADA = "4000000000000002"
TARJETA_FONDOS_INSUFICIENTES = "4000000000009995"

FIRMA_CABECERA = "X-Simulada-Firma"


class PasarelaSimulada(Pasarela):
    nombre = "simulada"

    def __init__(self, secreto_webhook, tolerancia_seg=300, modo="demo"):
        self._secreto = secreto_webhook
        self._tolerancia_seg = tolerancia_seg
        self.modo = modo
        self._pagos = {}  # id_pago_proveedor -> dict interno (solo en memoria)

    # ── Interfaz Pasarela ──────────────────────────────────────────

    def crear_pago(self, solicitud: PagoSolicitud) -> PagoResultado:
        clave = solicitud.clave_idempotencia or f"venta-{solicitud.id_venta}"
        id_pago_proveedor = f"sim_{clave}"
        existente = self._pagos.get(id_pago_proveedor)
        if existente:
            return self._resultado(existente)  # misma clave -> mismo pago
        self._pagos[id_pago_proveedor] = {
            "id_pago_proveedor": id_pago_proveedor,
            "id_venta": solicitud.id_venta,
            "monto": solicitud.monto,
            "moneda": solicitud.moneda,
            "metodo": solicitud.metodo,
            "estado": EstadoPago.PENDIENTE,
            "requiere_verificacion": solicitud.metodo != "tarjeta",
            "razon": "",
        }
        return self._resultado(self._pagos[id_pago_proveedor])

    def verificar_pago(self, id_pago_proveedor: str) -> PagoResultado:
        pago = self._pagos.get(id_pago_proveedor)
        if not pago:
            raise ValueError(f"id de pago de proveedor desconocido: {id_pago_proveedor}")
        return self._resultado(pago)

    def consultar_estado(self, id_pago_proveedor: str) -> EstadoPago:
        return self.verificar_pago(id_pago_proveedor).estado

    def procesar_webhook(self, payload: bytes, headers) -> EventoWebhook:
        recibido = datetime.now(timezone.utc)
        firma = (headers.get(FIRMA_CABECERA) or "").lower()
        esperada = hmac.new(self._secreto.encode(), payload, hashlib.sha256).hexdigest()
        firma_valida = bool(firma) and hmac.compare_digest(firma, esperada)
        try:
            datos = json.loads(payload.decode("utf-8"))
            if not isinstance(datos, dict):
                datos = {}
        except Exception:
            datos = {}
        estado = None
        if datos.get("estado"):
            try:
                estado = EstadoPago(str(datos["estado"]))
            except ValueError:
                estado = EstadoPago.DESCONOCIDO
        ocurre_en = recibido
        fuera_de_tiempo = False
        timestamp = datos.get("timestamp")
        if isinstance(timestamp, (int, float)):
            ocurre_en = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            fuera_de_tiempo = abs((recibido - ocurre_en).total_seconds()) > self._tolerancia_seg
        monto = None
        if datos.get("monto") is not None:
            try:
                monto = Decimal(str(datos["monto"]))
            except Exception:
                monto = None
        return EventoWebhook(
            id_evento=str(datos.get("id_evento") or ""),
            id_pago_proveedor=datos.get("id_pago") or None,
            estado=estado,
            monto=monto,
            ocurre_en=ocurre_en,
            firma_valida=firma_valida,
            fuera_de_tiempo=fuera_de_tiempo,
            detalle_sin_sensibles={"tipo": str(datos.get("tipo") or "")},
        )

    # ── Específicos de la demo (NO parte de la interfaz base) ──────

    def procesar_checkout(self, id_pago_proveedor, datos_checkout):
        """Evalúa una compra del checkout interno simulado.

        Específico de la demo: no forma parte de la interfaz Pasarela. Lo
        usará la página de pago simulado (Tramo 2) sobre la instancia que
        crea registro.crear_pasarela(). En yape/billetera el dinero 'llega'
        después: la confirmación solo por webhook. Nunca guarda la tarjeta.
        """
        pago = self._pagos.get(id_pago_proveedor)
        if not pago:
            raise ValueError(f"id de pago de proveedor desconocido: {id_pago_proveedor}")
        if pago["metodo"] != "tarjeta":
            return self._resultado(pago)
        numero = "".join(ch for ch in str(datos_checkout.get("numero_tarjeta") or "") if ch.isdigit())
        if numero == TARJETA_APROBADA:
            pago["estado"] = EstadoPago.CONFIRMADO
            pago["razon"] = "tarjeta de prueba aprobada"
        elif numero == TARJETA_FONDOS_INSUFICIENTES:
            pago["estado"] = EstadoPago.RECHAZADO
            pago["razon"] = "fondos insuficientes"
        else:
            pago["estado"] = EstadoPago.RECHAZADO
            pago["razon"] = ("tarjeta rechazada" if numero == TARJETA_RECHAZADA
                             else "tarjeta de prueba desconocida")
        return self._resultado(pago)

    def firmar_evento(self, id_pago_proveedor, estado, monto, tipo="pago_actualizado"):
        """Construye (payload, headers) firmados con HMAC, como haría el
        proveedor real. Para los tests y el gatillo del panel de demo (Tramo 2)."""
        datos = {
            "id_evento": f"evt-{id_pago_proveedor}-{int(time.time())}",
            "id_pago": id_pago_proveedor,
            "tipo": tipo,
            "estado": estado.value if isinstance(estado, EstadoPago) else str(estado),
            "monto": str(monto),
            "timestamp": time.time(),
        }
        payload = json.dumps(datos, separators=(",", ":")).encode("utf-8")
        firma = hmac.new(self._secreto.encode(), payload, hashlib.sha256).hexdigest()
        return payload, {FIRMA_CABECERA: firma}

    def _resultado(self, pago):
        return PagoResultado(
            id_pago_proveedor=pago["id_pago_proveedor"],
            estado=pago["estado"],
            monto=pago["monto"],
            moneda=pago["moneda"],
            checkout_url=f"/pago/simulado/{pago['id_pago_proveedor']}",
            requiere_verificacion=pago.get("requiere_verificacion", False),
            detalle={"metodo": pago["metodo"], "razon": pago.get("razon", "")},
        )
