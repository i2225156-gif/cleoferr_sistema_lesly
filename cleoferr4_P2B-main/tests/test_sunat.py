"""Fase 2.2: paquete services/sunat (simulado, apisperu, real stub, fábrica).

Tests 100 % unitarios: SIN BD, SIN llamadas de red reales (la API Perú se
simula con un mock; el token jamás se imprime).
"""
import json
import urllib.error
from decimal import Decimal

import pytest

import services.sunat.apisperu as apis_mod
from services.sunat import registro as registro_sunat
from services.sunat.base import (ComprobanteSolicitud, EstadoComprobanteSunat,
                                 ProveedorSunat)
from services.sunat.registro import crear_proveedor_sunat
from services.sunat.simulado import SunatSimulado, ruc_valido


def _sunat():
    return SunatSimulado(emisor_ruc="20605977074",
                         razon_social="CLEOFERR SIMULADA",
                         direccion="DIRECCION SIMULADA")


# ── RUC / DNI (deterministas) ──────────────────────────────────

def test_ruc_digito_verificador_modulo_11():
    # '20605977074' tiene DV válido (módulo 11); '...75' no.
    assert ruc_valido("20605977074") is True
    assert ruc_valido("20605977075") is False
    assert ruc_valido("2060597707") is False      # 10 dígitos
    assert ruc_valido("30605977074") is False     # prefijo inválido
    assert ruc_valido("2060597707A") is False


def test_consulta_ruc_determinista():
    s = _sunat()
    activo = s.consultar_ruc("20605977074")
    assert activo.valido and activo.estado == "activo"
    assert activo.razon_social.startswith("CONTRIBUYENTE SIMULADO")
    no_habido = s.consultar_ruc("20505050500")    # válido y termina en '00'
    assert no_habido.valido and no_habido.estado == "no_habido"
    inexistente = s.consultar_ruc("20605977075")  # DV inválido
    assert not inexistente.valido and inexistente.estado == "inexistente"


def test_consulta_dni_determinista():
    s = _sunat()
    ok = s.consultar_dni("12345678")
    assert ok.valido and ok.estado == "activo"
    assert ok.razon_social == "PERSONA SIMULADA 12345678"
    assert not s.consultar_dni("00000000").valido
    assert not s.consultar_dni("1234567").valido


# ── Emisión simulada ────────────────────────────────────────────

def _solicitud(tipo="boleta", serie="B001", numero=53, doc=None, total=Decimal("118.00")):
    return ComprobanteSolicitud(id_venta=99, tipo=tipo, serie=serie, numero=numero,
                                cliente_documento=doc, cliente_nombre="CLIENTES VARIOS",
                                subtotal=Decimal("100.00"), igv=Decimal("18.00"),
                                total=total)


def test_emitir_boleta_rotulada_como_simulada():
    r = _sunat().emitir_comprobante(_solicitud())
    assert r.estado is EstadoComprobanteSunat.EMITIDO
    assert r.estado.value == "enviado"          # valor existente en la BD
    assert r.referencia_proveedor.startswith("SIM-B001-000053-")
    assert r.hash.startswith("SIM-")
    assert "20605977074" in r.qr_contenido
    assert "SIMULADO - sin validez tributaria" in r.qr_contenido
    assert "sin validez tributaria" in r.cdr
    assert r.simulado is True


def test_emitir_factura_exige_ruc_valido():
    s = _sunat()
    with pytest.raises(ValueError):
        s.emitir_comprobante(_solicitud(tipo="factura", serie="F001", doc=None))
    with pytest.raises(ValueError):
        s.emitir_comprobante(_solicitud(tipo="factura", serie="F001", doc="20605977075"))
    r = s.emitir_comprobante(_solicitud(tipo="factura", serie="F001", doc="20605977074"))
    assert r.referencia_proveedor.startswith("SIM-F001-")


def test_emitir_valida_serie_y_correlativo():
    s = _sunat()
    with pytest.raises(ValueError):   # serie de boleta no corresponde a factura
        s.emitir_comprobante(_solicitud(tipo="factura", serie="B001", doc="20605977074"))
    with pytest.raises(ValueError):   # correlativo inválido
        s.emitir_comprobante(_solicitud(numero=0))
    with pytest.raises(ValueError):   # tipo desconocido
        s.emitir_comprobante(_solicitud(tipo="nota"))


def test_consultar_estado_y_anular():
    s = _sunat()
    r = s.emitir_comprobante(_solicitud())
    assert s.consultar_estado_comprobante(r.referencia_proveedor) is EstadoComprobanteSunat.EMITIDO
    with pytest.raises(ValueError):
        s.consultar_estado_comprobante("SIM-inexistente")
    with pytest.raises(NotImplementedError):   # definido pero sin implementar
        s.anular_comprobante(r.referencia_proveedor)


# ── Fábrica ─────────────────────────────────────────────────────

def test_fabrica_simulado_ok(monkeypatch):
    monkeypatch.setenv("SUNAT_PROVEEDOR", "simulado")
    monkeypatch.setenv("EMPRESA_RUC", "20605977074")
    monkeypatch.setenv("SUNAT_MODO", "simulado")
    p = crear_proveedor_sunat()
    assert p.nombre == "simulado"


def test_fabrica_simulado_sin_ruc_o_con_ejemplo(monkeypatch):
    monkeypatch.setenv("SUNAT_PROVEEDOR", "simulado")
    monkeypatch.delenv("EMPRESA_RUC", raising=False)
    with pytest.raises(RuntimeError):
        crear_proveedor_sunat()
    monkeypatch.setenv("EMPRESA_RUC", registro_sunat._RUC_EJEMPLO)
    with pytest.raises(RuntimeError):
        crear_proveedor_sunat()
    monkeypatch.setenv("EMPRESA_RUC", "20605977074")
    monkeypatch.setenv("SUNAT_MODO", "produccion")
    with pytest.raises(RuntimeError):
        crear_proveedor_sunat()


def test_fabrica_apisperu_y_errores(monkeypatch):
    monkeypatch.delenv("BASE_SUNAT", raising=False)
    monkeypatch.delenv("TOKEN_SUNAT", raising=False)
    with pytest.raises(RuntimeError):
        crear_proveedor_sunat("apisperu")
    monkeypatch.setenv("BASE_SUNAT", "https://ejemplo.test/api/v1")
    monkeypatch.setenv("TOKEN_SUNAT", "token-solo-para-test")
    assert crear_proveedor_sunat("apisperu").nombre == "apisperu"
    with pytest.raises(ValueError):
        crear_proveedor_sunat("otro")
    real = crear_proveedor_sunat("real")
    with pytest.raises(NotImplementedError):
        real.emitir_comprobante(_solicitud())


# ── apisperu con la red simulada (token nunca impreso) ──────────

class _Resp:
    def __init__(self, datos):
        self._datos = datos

    def read(self):
        return json.dumps(self._datos).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_apisperu_consulta_ruc_ok(monkeypatch):
    monkeypatch.setenv("BASE_SUNAT", "https://ejemplo.test/api/v1")
    monkeypatch.setenv("TOKEN_SUNAT", "token-solo-para-test")
    monkeypatch.setattr(apis_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(
                            {"razonSocial": "EMPRESA DE PRUEBA", "ubigeo": "150101"}))
    p = crear_proveedor_sunat("apisperu")
    r = p.consultar_ruc("20605977074")
    assert r.valido and r.razon_social == "EMPRESA DE PRUEBA" and r.simulado is False
    with pytest.raises(NotImplementedError):     # D5: solo consultas
        p.emitir_comprobante(_solicitud())


def test_apisperu_errores_de_red_o_http(monkeypatch):
    monkeypatch.setenv("BASE_SUNAT", "https://ejemplo.test/api/v1")
    monkeypatch.setenv("TOKEN_SUNAT", "token-solo-para-test")
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("url", 404, "Not Found", None, None)
    monkeypatch.setattr(apis_mod.urllib.request, "urlopen", _raise)
    p = crear_proveedor_sunat("apisperu")
    assert p.consultar_ruc("20605977074").valido is False
    assert p.consultar_dni("12345678").valido is False


def test_interfaz_es_abstracta():
    with pytest.raises(TypeError):
        ProveedorSunat()
