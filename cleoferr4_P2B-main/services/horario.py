"""Horario de la tienda: día y pared calendario en America/Lima.

Las columnas de fecha de la BD son timestamptz. El modismo legado
``col AT TIME ZONE 'UTC' AT TIME ZONE 'America/Lima'`` estaba pensado
para columnas naive-UTC: sobre timestamptz resuelve la pared UTC, no la
Lima, así que los filtros ``= CURRENT_DATE`` perdían las ventas de
19:00 a 24:00 Lima y los displays mostraban la hora +5h. Estas
plantillas son la forma correcta para timestamptz; se usan con
.format como SQL_IMPORTE_LINEA (services/ofertas.py):

    SQL_DIA_LIMA.format(p='v.', col='fecha')    -- día calendario Lima
    SQL_PARED_LIMA.format(p='v.', col='fecha')  -- timestamp naive Lima

SQL_HOY_LIMA no lleva parámetros (NOW() no tiene alias de tabla).
"""
SQL_DIA_LIMA = "({p}{col} AT TIME ZONE 'America/Lima')::date"
SQL_HOY_LIMA = "(NOW() AT TIME ZONE 'America/Lima')::date"
SQL_PARED_LIMA = "({p}{col} AT TIME ZONE 'America/Lima')"


def hoy_lima():
    """Hoy en America/Lima (Peru no tiene horario de verano: UTC-5)."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def validar_rango_fechas(desde, hasta, hoy=None, dias_por_defecto=30):
    """Politica UNICA del rango de fechas de los reportes (dia Lima).

    - Sin fechas: los ultimos `dias_por_defecto` dias (hoy incluido).
    - «hasta» no puede estar en el futuro: se recorta a hoy (aviso).
    - «desde» no puede estar en el futuro: se recorta a hoy (aviso).
    - «desde» posterior a «hasta»: se intercambian (aviso) para que el
      rango siempre sea coherente (desde <= hasta).
    - Formato invalido: caso seguro (el campo se ignora y el rango
      vuelve al por defecto para lo que falte) + aviso.

    Devuelve (desde, hasta, aviso): fechas `date` de Lima y el aviso
    (o None). Nunca lanza: una entrada sucia no puede romper la pagina.
    """
    from datetime import datetime, timedelta
    hoy = hoy or hoy_lima()
    avisos = []

    def parse(valor):
        if valor is None:
            return None
        texto = str(valor).strip()
        if not texto:
            return None
        try:
            return datetime.strptime(texto, '%Y-%m-%d').date()
        except ValueError:
            avisos.append('Fecha inválida, se usó el rango por defecto.')
            return None

    d = parse(desde)
    h = parse(hasta)
    if h is not None and h > hoy:
        avisos.append('«Hasta» no puede estar en el futuro: se usó hoy.')
        h = hoy
    if d is not None and d > hoy:
        avisos.append('«Desde» no puede estar en el futuro: se usó hoy.')
        d = hoy
    if d is None and h is None:
        d = hoy - timedelta(days=dias_por_defecto - 1)
        h = hoy
    elif d is None:
        d = h - timedelta(days=dias_por_defecto - 1)
    elif h is None:
        h = hoy
    if d > h:
        avisos.append('Rango inválido: «desde» es posterior a «hasta»; se corrigieron las fechas.')
        d, h = h, d
    return d, h, (' '.join(avisos) or None)
