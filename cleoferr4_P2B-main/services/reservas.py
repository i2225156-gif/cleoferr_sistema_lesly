"""Reservas de stock vencidas (Fase C): liberar pedidos sin pagar.

Reglas:
- Plazos desde venta.fecha, SOLO mientras el pago siga 'pendiente':
    * tarjeta: 30 minutos
    * efectivo / yape_plin / N.o de operación: 24 horas
- NO tocar: pagos 'rechazado' (reintento D3), ventas confirmadas, POS
  (tipo_venta='local').
- Por venta: transacción con FOR UPDATE; venta->'cancelado',
  pago->'cancelado', stock devuelto con el helper B2 idempotente.
- El trigger trg_auditar_estado_venta registra el cambio en auditoria.
- Idempotente: ejecutar dos veces no repite efectos (el guardia del
  UPDATE de venta + la marca de B2 lo garantizan).
"""
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# Plazos por tipo de pago (en segundos), leídos del entorno con defaults.
_PLAZOS_DEFECTO = {
    'tarjeta': 30 * 60,        # 30 minutos
    'efectivo': 24 * 60 * 60,  # 24 horas
    'yape_plin': 24 * 60 * 60, # 24 horas
}


def _plazo_segundos(tipo_pago):
    """Plazo en segundos según el tipo de pago (configurable via .env)."""
    import os
    if tipo_pago == 'tarjeta':
        return int(os.environ.get('RESERVA_PLAZO_TARJETA_SEG',
                                  str(_PLAZOS_DEFECTO['tarjeta'])))
    return int(os.environ.get('RESERVA_PLAZO_OPERACION_SEG',
                              str(_PLAZOS_DEFECTO['efectivo'])))


def _ventas_vencidas_sql():
    """SQL que encuentra ventas pendientes con pago vencido.

    Une venta + pago + tipo_pago para calcular el plazo según el método.
    Excluye: tipo_venta='local' (POS), pagos que no están 'pendiente'.
    """
    return """
        SELECT v.id_venta,
               v.fecha,
               tp.nombre AS tipo_pago,
               p.id_pago,
               EXTRACT(EPOCH FROM (NOW() - v.fecha)) AS segundos_transcurridos,
               CASE WHEN tp.nombre = 'tarjeta'
                    THEN %s
                    ELSE %s
               END AS plazo_segundos
          FROM venta v
          JOIN pago p ON p.id_venta = v.id_venta AND p.estado = 'pendiente'
          JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
          JOIN tipo_pago tp ON tp.id_tipo_pago = p.id_tipo_pago
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
         WHERE ev.nombre = 'pendiente'
           AND tv.nombre <> 'local'
           AND v.fecha + CASE WHEN tp.nombre = 'tarjeta'
                              THEN make_interval(secs => %s)
                              ELSE make_interval(secs => %s)
                         END < NOW()
    """


def total_reservas_vencidas(cursor):
    """Solo lectura: COUNT del conjunto COMPLETO de vencidas.

    Envuelve el MISMO SQL del preview para que el número del panel no
    pueda desincronizarse del conjunto que libera el botón.
    """
    plazo_tarjeta = _plazo_segundos('tarjeta')
    plazo_operacion = _plazo_segundos('efectivo')
    cursor.execute(
        "SELECT COUNT(*) AS total FROM (" + _ventas_vencidas_sql() + ") t",
        (plazo_tarjeta, plazo_operacion, plazo_tarjeta, plazo_operacion))
    fila = cursor.fetchone()
    total = fila['total'] if isinstance(fila, dict) else fila[0]
    return int(total)


def preview_reservas_vencidas(cursor, limite=None, offset=0):
    """Solo lectura: lista las ventas que LIBERARIA el botón.

    Devuelve lista de dicts con id_venta, tipo_pago, segundos_transcurridos
    y plazo. SIN datos personales. Con limite devuelve solo esa página del
    historial (ORDER BY estable fecha DESC + id DESC); sin limite, el
    conjunto completo como siempre. liberar_reservas_vencidas NO usa
    esto: sigue operando sobre el conjunto completo.
    """
    plazo_tarjeta = _plazo_segundos('tarjeta')
    plazo_operacion = _plazo_segundos('efectivo')
    sql = _ventas_vencidas_sql()
    params = [plazo_tarjeta, plazo_operacion, plazo_tarjeta, plazo_operacion]
    if limite is not None:
        sql += " ORDER BY v.fecha DESC, v.id_venta DESC LIMIT %s OFFSET %s"
        params += [limite, offset]
    cursor.execute(sql, params)
    filas = cursor.fetchall()
    resultado = []
    for f in filas:
        d = f if isinstance(f, dict) else dict(zip(
            ['id_venta', 'fecha', 'tipo_pago', 'id_pago',
             'segundos_transcurridos', 'plazo_segundos'], f))
        resultado.append({
            'id_venta': d['id_venta'],
            'tipo_pago': d['tipo_pago'],
            'segundos_transcurridos': int(float(d['segundos_transcurridos'])),
            'plazo_segundos': int(float(d['plazo_segundos'])),
        })
    return resultado


def liberar_reservas_vencidas(conn):
    """Ejecuta la liberación de TODAS las reservas vencidas.

    Una sola transacción. Por cada venta:
    1. FOR UPDATE sobre la fila de venta (bloquea contra cambios concurrentes)
    2. Verifica que SIGA pendiente y que su pago SIGA pendiente
    3. venta->'cancelado', pago->'cancelado', stock devuelto con B2
    4. Todo protegido por el guardia del UPDATE + la marca de B2 (idempotente)

    Devuelve {'liberadas': int, 'ids': [id_venta, ...], 'errores': int}.
    """
    cursor = conn.cursor(dictionary=True)

    # 1) Vista previa de lo que hay que liberar
    plazo_tarjeta = _plazo_segundos('tarjeta')
    plazo_operacion = _plazo_segundos('efectivo')
    cursor.execute(_ventas_vencidas_sql(),
                    (plazo_tarjeta, plazo_operacion,
                     plazo_tarjeta, plazo_operacion))
    vencidas = cursor.fetchall()

    if not vencidas:
        return {'liberadas': 0, 'ids': [], 'errores': 0}

    ids_liberadas = []
    errores = 0

    from .inventario import devolver_stock_venta

    for v in vencidas:
        vid = v['id_venta']
        try:
            # 2) Bloquear la fila y RE-verificar (pudo cambiar desde el SELECT)
            cursor.execute("""
                SELECT ev.nombre AS estado_venta, p.estado AS estado_pago
                  FROM venta v
                  JOIN pago p ON p.id_venta = v.id_venta
                  JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
                 WHERE v.id_venta = %s
                   FOR UPDATE OF v, p
            """, (vid,))
            fila = cursor.fetchone()
            if not fila or fila['estado_venta'] != 'pendiente' or fila['estado_pago'] != 'pendiente':
                continue   # ya cambió entre la vista previa y el lock

            # 3) Cancelar venta (el guardia <> cancelado lo hace idempotente)
            cursor.execute("""
                UPDATE venta
                   SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta
                                            WHERE nombre = 'cancelado')
                 WHERE id_venta = %s
                   AND id_estado_venta <> (SELECT id_estado_venta FROM estado_venta
                                             WHERE nombre = 'cancelado')
                RETURNING id_venta
            """, (vid,))
            if not cursor.fetchone():
                continue   # ya estaba cancelada (idempotente)

            # Stock devuelto con el helper B2 (idempotente por marca)
            devolver_stock_venta(cursor, vid)

            # Pago -> cancelado
            cursor.execute(
                "UPDATE pago SET estado = 'cancelado' WHERE id_venta = %s AND estado = 'pendiente'",
                (vid,))

            ids_liberadas.append(vid)
        except Exception:
            errores += 1
            logger.exception("Error liberando la reserva de la venta %s", vid)

    conn.commit()
    logger.info("Reservas liberadas: %d (ids=%s, errores=%d)",
                len(ids_liberadas), ids_liberadas, errores)
    return {'liberadas': len(ids_liberadas), 'ids': ids_liberadas, 'errores': errores}
