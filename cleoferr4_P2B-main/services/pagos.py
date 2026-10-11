"""Negocio de pagos (Fase 2.1, Tramo 2): la ÚNICA puerta a 'confirmado'.

Reglas (paso 2b aprobado):
- confirmar_pago es la única vía del código para pasar un pago a
  'confirmado'; nadie más escribe ese estado en pago.estado.
- Transacción única con bloqueo de fila (SELECT ... FOR UPDATE OF pago,
  venta). Un segundo intento concurrente espera hasta lock_timeout y
  devuelve 'bloqueado' sin efectos, o ve el estado final y no duplica
  nada (idempotente).
- Valida ANTES de confirmar: id de pago del proveedor == pago.id_transaccion;
  monto del proveedor == pago.monto == total recalculado de detalle_venta.
  Si no cuadra: no confirma y lo registra en pago_evento y auditoria.
- Pago aprobado sobre venta CANCELADA: NO resucita la venta; marca el caso
  (pago_evento + auditoria) para revisión manual del administrador
  (opciones: reembolsar o reponer stock).
- 'desconocido' NUNCA se escribe en pago.estado (solo consta como
  estado_resultante en pago_evento).
"""
import json
import os
import re
import uuid
from decimal import Decimal

import db_supabase
from psycopg2 import errors as _pg_errores

from .pasarela.base import EstadoPago, PagoResultado

_LOCK_DEFECTO = "5s"
_RE_TIMEOUT = re.compile(r"^\d+(\.\d+)?(s|ms)?$")


def _lock_timeout():
    valor = os.environ.get("PAGO_LOCK_TIMEOUT", _LOCK_DEFECTO)
    return valor if _RE_TIMEOUT.match(valor) else _LOCK_DEFECTO


def _valor(fila, clave, indice=0):
    if fila is None:
        return None
    if isinstance(fila, dict):
        return fila[clave]
    return fila[indice]


def registrar_evento(cursor, proveedor, id_evento, tipo, id_pago,
                    estado_resultante, firma_valida, dentro_de_tolerancia,
                    detalle):
    """Reclama un evento del webhook (idempotencia por id de evento).

    INSERT ... ON CONFLICT DO NOTHING: devuelve el id de la fila nueva o
    None si el evento ya estaba registrado (reenvio). El detalle viaja
    REDACTADO: solo claves neutrales, jamas payload crudo ni secretos.
    """
    cursor.execute("""
        INSERT INTO pago_evento
            (proveedor, id_evento_proveedor, tipo, id_pago, estado_resultante,
             firma_valida, dentro_de_tolerancia, detalle)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (proveedor, id_evento_proveedor) DO NOTHING
        RETURNING id_pago_evento
    """, (proveedor, id_evento, tipo, id_pago, estado_resultante,
          bool(firma_valida), bool(dentro_de_tolerancia),
          json.dumps(detalle, ensure_ascii=False)))
    fila = cursor.fetchone()
    return _valor(fila, "id_pago_evento")


def _detalle_neutral(resultado, fuente):
    """Claves neutrales para pago.detalle_proveedor (regla A.3)."""
    d = {"fuente": fuente}
    det = dict(resultado.detalle or {})
    for clave in ("metodo", "razon"):
        if det.get(clave):
            d[clave] = det[clave]
    return d


def confirmar_pago(id_pago, resultado, conn=None, fuente="retorno",
                   id_pago_evento=None):
    """ÚNICA puerta del código para pasar un pago a 'confirmado'.

    conn=None (producción): abre su propia conexión y hace commit/rollback.
    conn inyectada (webhook/tests): la transacción la controla el caller.
    id_pago_evento: fila de pago_evento ya reclamada por el webhook.

    Devuelve {'estado': ..., 'id_pago': ..., 'motivo': ...} con estado en:
    confirmado | ya_confirmado | rechazado | ya_rechazado | invalido |
    venta_cancelada | ignorado | bloqueado | no_existe.
    """
    propia = conn is None
    if propia:
        conn = db_supabase.get_connection_tienda()
    cur = conn.cursor(dictionary=True)

    def _auditar(accion, valor_anterior, valor_nuevo):
        cur.execute("""
            INSERT INTO auditoria
                (id_usuario, tabla_afectada, id_registro, accion,
                 valor_anterior, valor_nuevo)
            VALUES (NULL, 'pago', %s, %s, %s, %s)
        """, (id_pago, accion, valor_anterior, valor_nuevo))

    def _marcar_evento(motivo, procesado=False):
        if id_pago_evento:
            cur.execute("""
                UPDATE pago_evento
                   SET detalle = detalle || %s::jsonb,
                       procesado_en = CASE WHEN %s THEN COALESCE(procesado_en, NOW())
                                           ELSE procesado_en END
                 WHERE id_pago_evento = %s
            """, (json.dumps({"motivo": motivo}, ensure_ascii=False),
                  bool(procesado), id_pago_evento))

    try:
        if not propia:
            # Con conn inyectada (webhook/tests) el caller controla la
            # transaccion: un error aqui no debe abortarla entera.
            cur.execute("SAVEPOINT sp_confirmar_pago")
        cur.execute("SET LOCAL lock_timeout = '%s'" % _lock_timeout())
        cur.execute("""
            SELECT p.id_venta, p.pasarela, p.estado AS pago_estado,
                   p.monto, p.id_transaccion, ev.nombre AS venta_estado
              FROM pago p
              JOIN venta v ON v.id_venta = p.id_venta
              JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
             WHERE p.id_pago = %s
               FOR UPDATE OF p, v
        """, (id_pago,))
        pago = cur.fetchone()
        if not pago:
            return {"estado": "no_existe", "id_pago": id_pago}

        estado = resultado.estado

        if estado not in (EstadoPago.CONFIRMADO, EstadoPago.RECHAZADO):
            _marcar_evento(f"estado '{estado.value if estado else 'None'}' no procesable")
            if propia:
                conn.commit()
            return {"estado": "ignorado", "id_pago": id_pago,
                    "motivo": "estado no procesable por confirmar_pago"}

        if estado is EstadoPago.CONFIRMADO:
            if pago["pago_estado"] == "confirmado":
                _marcar_evento("pago ya estaba confirmado", procesado=True)
                if propia:
                    conn.commit()
                return {"estado": "ya_confirmado", "id_pago": id_pago}
            if pago["pago_estado"] != "pendiente":
                _marcar_evento(f"confirmacion ignorada: pago ya estaba {pago['pago_estado']}")
                _auditar("confirmacion_ignorada", pago["pago_estado"], pago["pago_estado"])
                if propia:
                    conn.commit()
                return {"estado": "ignorado", "id_pago": id_pago,
                        "motivo": f"pago ya estaba {pago['pago_estado']}"}

            if pago["venta_estado"] == "cancelado":
                # Opcion iii: marcar, avisar, NO resucitar la venta.
                _marcar_evento("pago aprobado sobre venta cancelada: revision manual "
                               "(opciones: reembolsar o reponer stock)", procesado=True)
                _auditar("pago_sobre_venta_cancelada", "pendiente", "pendiente")
                if propia:
                    conn.commit()
                return {"estado": "venta_cancelada", "id_pago": id_pago,
                        "motivo": "venta cancelada: caso marcado para revision manual"}

            motivo = _validar(cur, pago, resultado)
            if motivo:
                _marcar_evento(f"validacion rechazada: {motivo}")
                _auditar("validacion_rechazada", "pendiente", "pendiente")
                if propia:
                    conn.commit()
                return {"estado": "invalido", "id_pago": id_pago, "motivo": motivo}

            cur.execute("""
                UPDATE pago
                   SET estado = 'confirmado',
                       confirmado_en = NOW(),
                       detalle_proveedor = COALESCE(detalle_proveedor, '{}'::jsonb) || %s::jsonb
                 WHERE id_pago = %s
            """, (json.dumps(_detalle_neutral(resultado, fuente), ensure_ascii=False), id_pago))
            cur.execute("""
                UPDATE venta
                   SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
                                            WHERE nombre = 'confirmado')
                 WHERE id_venta = %s
                   AND id_estado_venta <> (SELECT id_estado_venta FROM estado_venta
                                             WHERE nombre = 'confirmado')
            """, (pago["id_venta"],))
            _marcar_evento("confirmado", procesado=True)
            if id_pago_evento:
                cur.execute("UPDATE pago_evento SET id_pago = %s WHERE id_pago_evento = %s",
                            (id_pago, id_pago_evento))
            if propia:
                conn.commit()
            return {"estado": "confirmado", "id_pago": id_pago}

        # RECHAZADO
        if pago["pago_estado"] == "pendiente":
            cur.execute("""
                UPDATE pago
                   SET estado = 'rechazado',
                       detalle_proveedor = COALESCE(detalle_proveedor, '{}'::jsonb) || %s::jsonb
                 WHERE id_pago = %s
            """, (json.dumps(_detalle_neutral(resultado, fuente), ensure_ascii=False), id_pago))
            _marcar_evento("rechazado", procesado=True)
            if propia:
                conn.commit()
            return {"estado": "rechazado", "id_pago": id_pago}
        _marcar_evento(f"rechazo ignorado: pago ya estaba {pago['pago_estado']}",
                       procesado=True)
        if propia:
            conn.commit()
        return {"estado": "ya_rechazado" if pago["pago_estado"] == "rechazado" else "ignorado",
                "id_pago": id_pago}

    except _pg_errores.LockNotAvailable:
        if propia:
            conn.rollback()
        else:
            cur.execute("ROLLBACK TO SAVEPOINT sp_confirmar_pago")
        return {"estado": "bloqueado", "id_pago": id_pago,
                "motivo": "otra confirmacion en curso (lock_timeout)"}
    except Exception:
        if propia:
            conn.rollback()
        else:
            try:
                cur.execute("ROLLBACK TO SAVEPOINT sp_confirmar_pago")
            except Exception:
                pass
        raise
    finally:
        if propia:
            conn.close()


def _validar(cur, pago, resultado):
    """Validaciones previas a confirmar. Devuelve None si todo cuadra."""
    if not resultado.id_pago_proveedor or not pago["id_transaccion"]:
        return "falta id de transaccion (proveedor o pago)"
    if str(resultado.id_pago_proveedor) != str(pago["id_transaccion"]):
        return "id de pago del proveedor no coincide con pago.id_transaccion"
    cur.execute("""
        SELECT COALESCE(SUM(cantidad * precio_unitario), 0) AS total
          FROM detalle_venta WHERE id_venta = %s
    """, (pago["id_venta"],))
    total = cur.fetchone()["total"]
    if resultado.monto is None or pago["monto"] is None:
        return "monto ausente (proveedor o pago)"
    if Decimal(str(resultado.monto)) != Decimal(str(pago["monto"])):
        return "monto del proveedor != pago.monto"
    if Decimal(str(pago["monto"])) != Decimal(str(total)):
        return "pago.monto != total recalculado de detalle_venta"
    return None
