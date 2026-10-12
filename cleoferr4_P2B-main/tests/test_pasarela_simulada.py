"""Tramo 1: comportamiento determinista de la pasarela simulada."""
from decimal import Decimal

import pytest

from services.pasarela.base import EstadoPago, PagoSolicitud
from services.pasarela.simulada import (TARJETA_APROBADA, TARJETA_FONDOS_INSUFICIENTES,
                                        TARJETA_RECHAZADA, PasarelaSimulada)


def _pasarela():
    return PasarelaSimulada(secreto_webhook="secreto-test", modo="demo")


def _solicitud(venta=1, metodo="tarjeta", clave="k1"):
    return PagoSolicitud(id_venta=venta, monto=Decimal("100.00"),
                         metodo=metodo, clave_idempotencia=clave)


def test_crear_pago_es_determinista_e_idempotente_por_clave():
    p = _pasarela()
    r1 = p.crear_pago(_solicitud())
    r2 = p.crear_pago(_solicitud())
    assert r1.id_pago_proveedor == r2.id_pago_proveedor == "sim_k1"
    assert r1.estado is EstadoPago.PENDIENTE
    assert r1.checkout_url == "/pago/simulado/sim_k1"


def test_tarjetas_de_prueba_deterministas():
    p = _pasarela()
    ok = p.crear_pago(_solicitud(venta=1, clave="ok"))
    res = p.procesar_checkout(ok.id_pago_proveedor, {"numero_tarjeta": TARJETA_APROBADA})
    assert res.estado is EstadoPago.CONFIRMADO
    assert res.detalle["razon"] == "tarjeta de prueba aprobada"

    rechazada = p.crear_pago(_solicitud(venta=2, clave="no"))
    res = p.procesar_checkout(rechazada.id_pago_proveedor, {"numero_tarjeta": TARJETA_RECHAZADA})
    assert res.estado is EstadoPago.RECHAZADO
    assert res.detalle["razon"] == "tarjeta rechazada"

    fondos = p.crear_pago(_solicitud(venta=3, clave="fondos"))
    res = p.procesar_checkout(fondos.id_pago_proveedor, {"numero_tarjeta": TARJETA_FONDOS_INSUFICIENTES})
    assert res.estado is EstadoPago.RECHAZADO
    assert res.detalle["razon"] == "fondos insuficientes"


def test_tarjeta_desconocida_se_rechaza():
    p = _pasarela()
    pid = p.crear_pago(_solicitud(clave="rara")).id_pago_proveedor
    res = p.procesar_checkout(pid, {"numero_tarjeta": "1234 5678 1234 5678"})
    assert res.estado is EstadoPago.RECHAZADO
    assert res.detalle["razon"] == "tarjeta de prueba desconocida"


def test_yape_plin_queda_pendiente_y_exige_verificacion():
    p = _pasarela()
    res = p.crear_pago(_solicitud(metodo="yape_plin", clave="yape"))
    assert res.requiere_verificacion is True
    out = p.procesar_checkout(res.id_pago_proveedor, {})
    assert out.estado is EstadoPago.PENDIENTE


def test_verificar_pago_y_consultar_estado():
    p = _pasarela()
    pid = p.crear_pago(_solicitud(clave="ver")).id_pago_proveedor
    p.procesar_checkout(pid, {"numero_tarjeta": TARJETA_APROBADA})
    assert p.consultar_estado(pid) is EstadoPago.CONFIRMADO
    assert p.verificar_pago(pid).id_pago_proveedor == pid


def test_id_de_pago_desconocido_lanza_valueerror():
    p = _pasarela()
    with pytest.raises(ValueError):
        p.verificar_pago("sim_inexistente")
    with pytest.raises(ValueError):
        p.procesar_checkout("sim_inexistente", {})
