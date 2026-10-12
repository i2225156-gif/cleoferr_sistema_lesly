"""SUNAT SIMULADO (Fase 2.2): sin llamadas reales; artefactos rotulados.

- RUC: algoritmo oficial módulo 11 (pesos 5,4,3,2,7,6,5,4,3,2;
  resultado 10 -> 0 y 11 -> 1). Prefijos aceptados 10 y 20 (coherente con
  las reglas de la app).
- Casos DETERMINISTAS para la demo (reglas simples, documentadas aquí):
    * dígito verificador inválido  -> inexistente
    * RUC válido terminado en '00' -> no_habido
    * cualquier otro RUC válido    -> activo
    * DNI de 8 dígitos              -> válido; '00000000' -> inexistente
  Los 'nombres' devueltos son SIEMPRE etiquetas '... SIMULADO ...':
  jamás datos reales de personas.
- Emisión: hace suyos la serie y el correlativo asignados por el llamador
  y devuelve hash/QR/CDR/referencia rotulados 'SIMULADO - sin validez
  tributaria'. La FACTURA exige RUC válido (regla SUNAT); la boleta admite
  cliente sin documento ('CLIENTES VARIOS' lo decide el llamador).
"""
import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from .base import (ComprobanteResultado, ComprobanteSolicitud,
                   ConsultaDocumento, EstadoComprobanteSunat, ProveedorSunat)

_PESOS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def ruc_valido(ruc):
    """Dígito verificador módulo 11 de SUNAT (algoritmo público)."""
    if not (isinstance(ruc, str) and ruc.isdigit() and len(ruc) == 11):
        return False
    if ruc[:2] not in ("10", "20"):
        return False
    suma = sum(int(ruc[i]) * _PESOS[i] for i in range(10))
    digito = 11 - (suma % 11)
    if digito == 10:
        digito = 0
    elif digito == 11:
        digito = 1
    return digito == int(ruc[10])


class SunatSimulado(ProveedorSunat):
    nombre = "simulado"

    def __init__(self, emisor_ruc, razon_social="", direccion="", modo="simulado"):
        self.emisor_ruc = emisor_ruc
        self.emisor_razon_social = razon_social
        self.emisor_direccion = direccion
        self.modo = modo
        self._emitidos = {}   # referencia -> estado (solo en memoria, demo)

    def consultar_ruc(self, ruc):
        ruc = (ruc or "").strip()
        if not ruc_valido(ruc):
            return ConsultaDocumento(numero=ruc, valido=False,
                                      estado="inexistente", simulado=True)
        if ruc.endswith("00"):
            return ConsultaDocumento(numero=ruc, valido=True, estado="no_habido",
                                     razon_social=f"CONTRIBUYENTE SIMULADO {ruc}",
                                     simulado=True)
        return ConsultaDocumento(numero=ruc, valido=True, estado="activo",
                                  razon_social=f"CONTRIBUYENTE SIMULADO {ruc}",
                                  direccion="DIRECCIÓN SIMULADA",
                                  ubigeo="150101", simulado=True)

    def consultar_dni(self, dni):
        dni = (dni or "").strip()
        if not (dni.isdigit() and len(dni) == 8) or dni == "00000000":
            return ConsultaDocumento(numero=dni, valido=False,
                                      estado="inexistente", simulado=True)
        return ConsultaDocumento(numero=dni, valido=True, estado="activo",
                                  razon_social=f"PERSONA SIMULADA {dni}",
                                  simulado=True)

    def emitir_comprobante(self, solicitud):
        if not isinstance(solicitud, ComprobanteSolicitud):
            raise ValueError("solicitud inválida")
        if solicitud.tipo not in ("boleta", "factura"):
            raise ValueError(f"tipo de comprobante inválido: {solicitud.tipo}")
        prefijo = "B" if solicitud.tipo == "boleta" else "F"
        if not solicitud.serie.startswith(prefijo):
            raise ValueError(f"la serie {solicitud.serie} no corresponde a una {solicitud.tipo}")
        if not (isinstance(solicitud.numero, int) and solicitud.numero > 0):
            raise ValueError("correlativo inválido")
        if solicitud.tipo == "factura":
            if not solicitud.cliente_documento or not ruc_valido(solicitud.cliente_documento):
                raise ValueError("la FACTURA exige un RUC válido (11 dígitos, prefijo 10/20)")

        fecha = datetime.now(timezone.utc)
        numero_fmt = f"{solicitud.numero:06d}"
        tipo_codigo = "03" if solicitud.tipo == "boleta" else "01"
        total = solicitud.total if solicitud.total is not None else Decimal("0")
        base_hash = f"{self.emisor_ruc}|{solicitud.serie}|{numero_fmt}|{total}|{fecha.isoformat()}"
        hash_sim = "SIM-" + hashlib.sha256(base_hash.encode()).hexdigest()
        qr = (f"{self.emisor_ruc}|{tipo_codigo}|{solicitud.serie}|{numero_fmt}|"
              f"{total}|{fecha.strftime('%Y-%m-%d')}|SIMULADO - sin validez tributaria")
        cdr = f"CDR-SIM-{solicitud.serie}-{numero_fmt} (SIMULADO - sin validez tributaria)"
        referencia = f"SIM-{solicitud.serie}-{numero_fmt}-{uuid.uuid4().hex[:8]}"
        self._emitidos[referencia] = EstadoComprobanteSunat.EMITIDO
        return ComprobanteResultado(
            id_venta=solicitud.id_venta, serie=solicitud.serie,
            numero=solicitud.numero, estado=EstadoComprobanteSunat.EMITIDO,
            referencia_proveedor=referencia, hash=hash_sim, qr_contenido=qr,
            cdr=cdr, fecha_emision=fecha, simulado=True)

    def consultar_estado_comprobante(self, referencia):
        estado = self._emitidos.get(referencia)
        if estado is None:
            raise ValueError(f"referencia de comprobante desconocida: {referencia}")
        return estado
