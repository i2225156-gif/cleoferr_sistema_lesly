"""Tramo 1: firma y verificación de webhooks de la simulada (vía interfaz)."""
import hashlib
import hmac
import json
import time
from decimal import Decimal

from services.pasarela.base import EstadoPago
from services.pasarela.simulada import FIRMA_CABECERA, PasarelaSimulada


def _firmar_a_mano(datos):
    payload = json.dumps(datos, separators=(",", ":")).encode("utf-8")
    firma = hmac.new(b"secreto-test", payload, hashlib.sha256).hexdigest()
    return payload, {FIRMA_CABECERA: firma}


def test_webhook_con_firma_valida():
    p = PasarelaSimulada(secreto_webhook="secreto-test")
    payload, headers = p.firmar_evento("sim_k1", EstadoPago.CONFIRMADO, Decimal("100.00"))
    ev = p.procesar_webhook(payload, headers)
    assert ev.firma_valida is True
    assert ev.fuera_de_tiempo is False
    assert ev.estado is EstadoPago.CONFIRMADO
    assert ev.id_pago_proveedor == "sim_k1"
    assert ev.monto == Decimal("100.00")
    assert ev.id_evento  # el id del evento viaja para la idempotencia (pago_evento, Tramo 2)


def test_webhook_con_firma_invalida_queda_marcado():
    p = PasarelaSimulada(secreto_webhook="secreto-test")
    payload, headers = p.firmar_evento("sim_k1", EstadoPago.CONFIRMADO, Decimal("100.00"))
    ev = p.procesar_webhook(payload, {FIRMA_CABECERA: "deadbeef"})
    assert ev.firma_valida is False


def test_webhook_sin_cabecera_de_firma_es_invalido():
    p = PasarelaSimulada(secreto_webhook="secreto-test")
    payload, _ = p.firmar_evento("sim_k1", EstadoPago.CONFIRMADO, Decimal("100.00"))
    ev = p.procesar_webhook(payload, {})
    assert ev.firma_valida is False


def test_webhook_fuera_de_tolerancia_de_tiempo():
    p = PasarelaSimulada(secreto_webhook="secreto-test", tolerancia_seg=60)
    payload, headers = _firmar_a_mano({
        "id_evento": "evt-viejo", "id_pago": "sim_k1", "tipo": "pago_actualizado",
        "estado": "confirmado", "monto": "100.00", "timestamp": time.time() - 3600,
    })
    ev = p.procesar_webhook(payload, headers)
    assert ev.firma_valida is True
    assert ev.fuera_de_tiempo is True


def test_estado_no_mapeable_es_desconocido_y_nunca_se_persiste():
    p = PasarelaSimulada(secreto_webhook="secreto-test")
    payload, headers = _firmar_a_mano({
        "id_evento": "evt-raro", "id_pago": "sim_k1", "estado": "estado_inventado",
    })
    ev = p.procesar_webhook(payload, headers)
    assert ev.estado is EstadoPago.DESCONOCIDO
