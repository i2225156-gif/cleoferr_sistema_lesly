"""Negocio de comprobantes (Fase 2.2): numeración, QR y emisión D1.

- La numeración y la emisión corren SIEMPRE dentro de la transacción del
  caller (confirmar_pago o el flujo POS).
- Emisión idempotente por venta: si la venta ya tiene comprobante no se
  hace nada (UNIQUE id_venta como red de seguridad).
- El proveedor SUNAT (simulado) aporta hash/QR/CDR/referencia rotulados
  'SIMULADO - sin validez tributaria'. El emisor sale del .env
  (EMPRESA_RUC / EMPRESA_RAZON_SOCIAL / EMPRESA_DIRECCION): jamás
  hardcodeado.
- La FACTURA exige RUC válido (regla SUNAT); la boleta admite cliente
  sin documento ('CLIENTES VARIOS' lo decide el llamador).
"""
import json
import logging
import os
from decimal import Decimal

from .ofertas import SQL_IMPORTE_LINEA
from .sunat.base import ComprobanteSolicitud
from .sunat.registro import crear_proveedor_sunat

logger = logging.getLogger(__name__)

# Fail-fast al arrancar si falta EMPRESA_RUC, es el valor de ejemplo o
# SUNAT_MODO no es 'simulado' (validación en services/sunat/registro.py).
_PROVEEDOR = crear_proveedor_sunat()

EMISOR = {
    "ruc": _PROVEEDOR.emisor_ruc,
    "razon_social": getattr(_PROVEEDOR, "emisor_razon_social", "") or "",
    "direccion": getattr(_PROVEEDOR, "emisor_direccion", "") or "",
}

# Umbral de venta para boletas sin documento del comprador ("CLIENTES
# VARIOS"). Regla de NEGOCIO configurable (no confirmada como exigencia
# SUNAT vigente — ver .env.example). 0 = no exigir documento nunca.
# Valor por defecto marcado "A VERIFICAR".
UMBRAL_BOLETA_SIN_DOC = Decimal(os.environ.get("UMBRAL_BOLETA_SIN_DOC", "700"))


def boleta_necesita_documento(total, cliente_documento):
    """True si el total supera el umbral y no hay DNI/RUC del cliente.

    Regla de negocio (fase B): debajo del umbral, la boleta se emite a
    "CLIENTES VARIOS" sin exigir documento; encima, el checkout debe
    rechazar la venta sin documento. El umbral es configurable y su
    valor está marcado A VERIFICAR (ver .env.example).
    """
    if cliente_documento and cliente_documento.strip():
        return False   # tiene documento: no importa el monto
    return Decimal(str(total or 0)) > UMBRAL_BOLETA_SIN_DOC


def ruc_valido_para_factura(ruc):
    """True si el RUC es válido según el proveedor (módulo 11 en el simulado)."""
    if not ruc:
        return False
    return _PROVEEDOR.consultar_ruc(str(ruc).strip()).valido


def siguiente_numero(cursor, tipo_comp):
    """Correlativo por serie: 'B000123' / 'F000123' (movido desde app.py).

    Se calcula dentro de la transacción del caller a partir del máximo
    existente; uq_comprobante_serie_numero (db/07) es la red de seguridad
    frente a asignaciones concurrentes.
    """
    serie = 'F' if tipo_comp == 'factura' else 'B'
    cursor.execute("""
        SELECT COALESCE(MAX(CAST(SUBSTRING(numero FROM '^[BF](\\d+)') AS INTEGER)), 0) + 1 AS seq
        FROM comprobante WHERE numero LIKE %s
    """, (serie + '%',))
    row = cursor.fetchone()
    seq = row['seq'] if isinstance(row, dict) else row[0]
    return f"{serie}{seq:06d}"


def texto_qr(tipo, serie, dig, igv, total, fecha):
    """QR estilo SUNAT simulado, con rótulo de simulación (solo render)."""
    tipo_codigo = '03' if (tipo or 'boleta').lower() == 'boleta' else '01'
    return (f"{EMISOR['ruc']}|{tipo_codigo}|{serie}|{dig}|"
            f"{float(igv):.2f}|{float(total):.2f}|{fecha or ''}|"
            f"SIMULADO - sin validez tributaria")


def _totales_desde_detalle(cursor, id_venta):
    """Totales recalculados de detalle_venta (precios con IGV incluido).

    El total es el NETO con el descuento congelado (fórmula única de
    services.ofertas); Op. Gravada e IGV se derivan de ese neto.
    """
    cursor.execute(f"""
        SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
          FROM detalle_venta WHERE id_venta = %s
    """, (id_venta,))
    row = cursor.fetchone()
    total = Decimal(str(row['total'] if isinstance(row, dict) else row[0]))
    subtotal = (total / Decimal('1.18')).quantize(Decimal('0.01'))
    igv = (total - subtotal).quantize(Decimal('0.01'))
    return subtotal, igv, total


def emitir_comprobante_venta(cursor, id_venta, tipo, ruc_cliente=None,
                             subtotal=None, igv=None, total=None):
    """Emite (simuladamente) el comprobante de una venta PAGADA.

    Dentro de la TXN del caller. Idempotente por venta: si ya existe,
    devuelve la fila existente sin tocar nada. La FACTURA exige RUC
    válido; si no lo es, lanza ValueError (confirmar_pago la aísla con
    SAVEPOINT para no perder la confirmación del pago).
    """
    cursor.execute("SELECT * FROM comprobante WHERE id_venta = %s LIMIT 1", (id_venta,))
    existente = cursor.fetchone()
    if existente:
        return existente

    if tipo == 'factura' and not ruc_valido_para_factura(ruc_cliente):
        raise ValueError("la FACTURA exige un RUC válido")

    serie = 'F001' if tipo == 'factura' else 'B001'
    numero_str = siguiente_numero(cursor, tipo)
    correlativo = int(numero_str[1:])
    subtotal = subtotal if subtotal is not None else Decimal('0')
    igv = igv if igv is not None else Decimal('0')
    total = total if total is not None else Decimal('0')

    resultado = _PROVEEDOR.emitir_comprobante(ComprobanteSolicitud(
        id_venta=id_venta, tipo=tipo, serie=serie, numero=correlativo,
        cliente_documento=ruc_cliente if tipo == 'factura' else None,
        subtotal=subtotal, igv=igv, total=total))
    cursor.execute("""
        INSERT INTO comprobante
            (id_venta, id_tipo_comprobante, numero, ruc, serie,
             subtotal, igv, total, estado_sunat, referencia_proveedor)
        VALUES (%s, (SELECT id_tipo_comprobante FROM tipo_comprobante WHERE nombre = %s),
                %s, %s, %s, %s, %s, %s, 'enviado', %s)
        RETURNING *
    """, (id_venta, tipo, numero_str,
          ruc_cliente if tipo == 'factura' else None, serie,
          subtotal, igv, total, resultado.referencia_proveedor))
    return cursor.fetchone()


def emitir_comprobante_del_pago(cursor, pago):
    """Emisión D1 a partir del pago CONFIRMADO de la venta.

    El tipo (boleta/factura) y el RUC salen del detalle_proveedor del pago
    (los guarda el checkout); si faltan, se emite boleta. Los totales se
    recalculan de detalle_venta, nunca del navegador.
    """
    detalle = pago.get('detalle_proveedor') or {}
    if isinstance(detalle, str):
        try:
            detalle = json.loads(detalle)
        except Exception:
            detalle = {}
    tipo = detalle.get('tipo_comprobante') or 'boleta'
    ruc = detalle.get('ruc') or None
    cliente_documento = detalle.get('cliente_documento') or None

    subtotal, igv, total = _totales_desde_detalle(cursor, pago['id_venta'])

    # CLIENTES VARIOS (fase B): boleta sin documento del comprador se
    # emite a "CLIENTES VARIOS". El umbral se valida en el CHECKOUT,
    # no aquí (la emisión ocurre tras confirmar el pago).
    cliente_nombre = detalle.get('cliente_nombre') or None
    if tipo == 'boleta' and not cliente_documento:
        cliente_nombre = 'CLIENTES VARIOS'

    return emitir_comprobante_venta(cursor, pago['id_venta'], tipo,
                                    ruc_cliente=ruc, subtotal=subtotal,
                                    igv=igv, total=total)
