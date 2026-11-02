"""Módulo de acceso a datos unificado sobre Supabase (PostgreSQL).

Expone dos engines SQLAlchemy separados:

- ``engine_auth``: BD de autenticación (tablas: usuario, cliente, rol, sesion).
- ``engine_tienda``: BD de la tienda (tablas: producto, venta, detalle_venta,
  inventario_movimiento, categoria, marca, proveedor, caja, alertas, etc.).

Cada engine se construye a partir de su propia variable de entorno:
``DATABASE_URI_AUTH`` y ``DATABASE_URI_TIENDA``. No existe una DATABASE_URI
genérica: cada módulo de la aplicación debe usar el engine que corresponda.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

# B0: dos políticas de pool según el entorno.
#
# Local: QueuePool. El host de este proyecto es el pooler de Supabase en modo
# SESIÓN (puerto 5432), que tolera conexiones persistentes. El error
# "set_session cannot be used inside a transaction" que motivó NullPool se evita
# con el rollback defensivo de _CompatConnection.close() antes de devolver la
# conexión al pool, junto con pool_pre_ping.
#
# Serverless (Vercel): NullPool. Cada invocación es efímera y la plataforma
# escala instancias en paralelo; conservar conexiones multiplicaría el consumo
# del pooler de Supabase hasta agotar sus límites. NullPool abre una conexión
# por operación y la cierra al terminar.
from sqlalchemy.pool import NullPool, QueuePool

_POOL_SIZE_POR_DEFECTO = 5
_MAX_OVERFLOW_POR_DEFECTO = 10
_POOL_TIMEOUT_POR_DEFECTO = 30
_POOL_RECYCLE_POR_DEFECTO = 280


def _entero_env(nombre, defecto, minimo):
    """Lee un entero de entorno; si falta o no es válido, usa el defecto."""
    try:
        valor = int(os.environ.get(nombre, defecto))
    except (TypeError, ValueError):
        return defecto
    return valor if valor >= minimo else defecto


def en_serverless():
    """Vercel define esta variable en sus funciones serverless."""
    return bool(os.environ.get("VERCEL"))


def opciones_pool():
    """Opciones de engine SQLAlchemy según el entorno de ejecución."""
    if en_serverless():
        return {"poolclass": NullPool}
    return {
        "poolclass": QueuePool,
        "pool_size": _entero_env("DB_POOL_SIZE", _POOL_SIZE_POR_DEFECTO, 1),
        "max_overflow": _entero_env("DB_MAX_OVERFLOW",
                                    _MAX_OVERFLOW_POR_DEFECTO, 0),
        "pool_timeout": _entero_env("DB_POOL_TIMEOUT",
                                    _POOL_TIMEOUT_POR_DEFECTO, 1),
        "pool_recycle": _entero_env("DB_POOL_RECYCLE",
                                    _POOL_RECYCLE_POR_DEFECTO, 1),
        "pool_pre_ping": True,
    }


def _crear_engine(nombre_var):
    uri = os.environ.get(nombre_var)
    if not uri:
        raise RuntimeError(
            f"{nombre_var} no definida en el archivo .env. "
            "Esta aplicación requiere dos URIs de Supabase (Auth y Tienda) "
            "configuradas por separado."
        )
    return create_engine(uri, **opciones_pool())


# Engine para la BD de autenticación (usuario, cliente, rol, sesion)
engine_auth = _crear_engine("DATABASE_URI_AUTH")

# Engine para la BD de la tienda (producto, venta, inventario, caja, etc.)
engine_tienda = _crear_engine("DATABASE_URI_TIENDA")


class _CompatConnection:
    """Envuelve una conexión DBAPI de psycopg2 para ofrecer la misma interfaz
    que usaba db2.py (estilo mysql.connector): ``cursor(dictionary=True)``,
    ``commit``, ``rollback`` y ``close``.

    NOTA: los placeholders ``%s`` son válidos también en psycopg2, así que las
    consultas parametrizadas existentes siguen funcionando tal cual.
    """

    def __init__(self, raw):
        self._raw = raw

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            import psycopg2.extras
            return self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self._raw.cursor()

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        # psycopg2 abre una transacción implícita hasta en SELECT; si la devolvemos
        # así al pool, SQLAlchemy falla con "set_session cannot be used inside a
        # transaction" al reutilizarla. Rollback defensivo antes de devolverla.
        if getattr(self, '_closed', False):
            return  # tolerante a doble close()
        self._closed = True
        try:
            self._raw.rollback()
        except Exception:
            pass
        self._raw.close()


def get_connection_auth():
    """Conexión a la BD de autenticación (usuario, cliente, rol, sesion)."""
    return _CompatConnection(engine_auth.raw_connection())


def get_connection_tienda():
    """Conexión a la BD de la tienda (producto, venta, inventario, caja, etc.)."""
    return _CompatConnection(engine_tienda.raw_connection())
