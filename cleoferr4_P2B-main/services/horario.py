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
