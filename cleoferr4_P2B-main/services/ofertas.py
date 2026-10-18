"""Negocio de ofertas y descuentos (Fase 2.3): el precio vigente lo decide el servidor.

Reglas (diseño aprobado de la fase 2.3):
- El precio final y el descuento los calcula SIEMPRE el servidor; el
  navegador solo renderiza lo que le manda la BD (catálogo, carrito) y
  el checkout recalcula todo desde la BD (ignora los precios cliente).
- Una sola oferta VIGENTE por producto: la BD lo garantiza con el
  constraint de exclusión ex_oferta_vigencia (db/03) para ofertas
  activas solapadas; la consulta filtra activa + rango temporal con
  NOW() de la BD (reloj de la BD, no del cliente).
- El descuento se congela al comprar en detalle_venta (id_oferta +
  descuento_unitario, snapshot): cambios o borrado posterior de la
  oferta no alteran ventas pasadas. La BD acota
  0 <= descuento <= precio (chk_detalle_descuento db/03 +
  chk_detalle_descuento_coherente db/08).
- Redondeo: descuento POR UNIDAD a 2 decimales (ROUND_HALF_UP,
  idéntico al ROUND de PostgreSQL para valores positivos) y DESPUÉS
  se multiplica por cantidad. SQL_IMPORTE_LINEA es la fórmula ÚNICA
  del importe neto de línea: nadie debe volver a escribir
  cantidad * precio_unitario para dinero a cobrar.
"""
from decimal import Decimal, ROUND_HALF_UP

_DOS_DEC = Decimal("0.01")

# Fórmula ÚNICA del importe neto (con descuento congelado) de una línea
# de detalle_venta. Usar con el prefijo del alias de la tabla:
# SQL_IMPORTE_LINEA.format(p="d.")  (p="" si la consulta no usa alias).
SQL_IMPORTE_LINEA = (
    "({p}cantidad * ({p}precio_unitario - COALESCE({p}descuento_unitario, 0)))"
)

# Oferta vigente por producto (LATERAL). El CASE replica EXACTAMENTE
# calcular_descuento_unitario (mismo redondeo y clamp) para que SQL y
# Python jamás divergan (los tests de 2.3 lo verifican).
# {p} = prefijo de columna del producto ("p."), {a} = alias del LATERAL.
# El clamp del CASE es defensa ante ofertas precio_fijo legadas tras
# una baja de precio (el trigger solo valida al cambiar tipo/valor/
# producto, así que precio - valor puede quedar negativo).
SQL_PRECIO_VIGENTE = """\
LEFT JOIN LATERAL (
    SELECT o.id_oferta, o.tipo, o.valor,
           CASE o.tipo
               WHEN 'porcentaje' THEN
                   LEAST(ROUND({p}precio * o.valor / 100, 2), {p}precio)
               ELSE
                   GREATEST(LEAST({p}precio - o.valor, {p}precio), 0)
           END AS descuento_unitario
      FROM public.oferta o
     WHERE o.id_producto = {p}id_producto
       AND o.activa = TRUE
       AND NOW() >= o.fecha_inicio
       AND NOW() <  o.fecha_fin
     ORDER BY o.id_oferta
     LIMIT 1
) {a} ON TRUE"""


def _a_decimal(valor):
    """Decimal exacto desde Decimal, int, float o str (jamás binario directo)."""
    return Decimal(str(valor))


def _fila_dict(cursor, fila):
    """Normaliza a dict una fila dict (RealDictCursor) o tupla (cursor plano)."""
    if fila is None:
        return None
    if isinstance(fila, dict):
        return fila
    return dict(zip([c[0] for c in cursor.description], fila))


def calcular_descuento_unitario(precio, tipo, valor):
    """Descuento por unidad (Decimal, 2 decimales, ROUND_HALF_UP).

    Espejo Python del CASE de SQL_PRECIO_VIGENTE: deben coincidir
    SIEMPRE (los tests lo verifican). El redondeo es por unidad y a 2
    decimales; el importe de línea se calcula después como
    cantidad * (precio - descuento) vía SQL_IMPORTE_LINEA.
    Clamp defensivo 0 <= descuento <= precio: con datos válidos por la
    BD (valor > 0, porcentaje <= 100, precio_fijo < precio al crear)
    no se activa, pero un precio_fijo legado tras una baja de precio
    daría negativo y se congela como 0.
    """
    precio = _a_decimal(precio)
    valor = _a_decimal(valor)
    if tipo == 'porcentaje' and 0 < valor <= 100:
        dcto = (precio * valor / Decimal(100)).quantize(_DOS_DEC, ROUND_HALF_UP)
    elif tipo == 'precio_fijo':
        dcto = precio - valor
    else:
        return Decimal("0.00")
    return min(max(dcto, Decimal("0")), precio)


def oferta_vigente(cursor, id_producto):
    """Precio vigente de un producto o None si el producto no existe.

    Devuelve dict con: precio, id_oferta (None si no hay oferta
    vigente), tipo, valor, descuento_unitario (0 sin oferta) y
    precio_final. Llamar DENTRO de la transacción que congela el
    descuento en detalle_venta, para que la lectura y el INSERT vean
    el mismo estado de la oferta.
    """
    sql = f"""
        SELECT p.id_producto, p.precio,
               of.id_oferta, of.tipo, of.valor,
               COALESCE(of.descuento_unitario, 0) AS descuento_unitario,
               p.precio - COALESCE(of.descuento_unitario, 0) AS precio_final
          FROM public.producto p
          {SQL_PRECIO_VIGENTE.format(p="p.", a="of")}
         WHERE p.id_producto = %s
    """
    cursor.execute(sql, (id_producto,))
    return _fila_dict(cursor, cursor.fetchone())


def precios_vigentes(cursor, ids):
    """Lote de precios vigentes para mostrar (catálogo, carrito).

    Devuelve {id_producto: fila con la forma de oferta_vigente}.
    Solo lectura: el navegador renderiza estos valores, jamás los
    decide.
    """
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {}
    sql = f"""
        SELECT p.id_producto, p.precio,
               of.id_oferta, of.tipo, of.valor,
               COALESCE(of.descuento_unitario, 0) AS descuento_unitario,
               p.precio - COALESCE(of.descuento_unitario, 0) AS precio_final
          FROM public.producto p
          {SQL_PRECIO_VIGENTE.format(p="p.", a="of")}
         WHERE p.id_producto = ANY(%s)
    """
    cursor.execute(sql, (ids,))
    filas = cursor.fetchall()
    return {f['id_producto']: f
            for f in (_fila_dict(cursor, fila) for fila in filas)}
