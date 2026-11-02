"""Política de pool de BD por entorno, sin abrir conexiones reales.

Local conserva QueuePool (compatibilidad con las pruebas y el desarrollo);
en Vercel/serverless no se retienen conexiones para no agotar el pooler de
Supabase cuando la plataforma escala instancias efímeras.
"""
from sqlalchemy.pool import NullPool, QueuePool

import db_supabase

_VARIABLES = ("VERCEL", "DB_POOL_SIZE", "DB_MAX_OVERFLOW",
              "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE")


def _limpiar(monkeypatch):
    for variable in _VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def test_local_por_defecto_mantiene_compatibilidad(monkeypatch):
    _limpiar(monkeypatch)

    opciones = db_supabase.opciones_pool()

    assert opciones["poolclass"] is QueuePool
    assert opciones["pool_size"] == 5
    assert opciones["max_overflow"] == 10
    assert opciones["pool_pre_ping"] is True


def test_local_permite_ajustar_el_pool_por_variables(monkeypatch):
    _limpiar(monkeypatch)
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "4")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "15")
    monkeypatch.setenv("DB_POOL_RECYCLE", "120")

    opciones = db_supabase.opciones_pool()

    assert opciones["poolclass"] is QueuePool
    assert opciones["pool_size"] == 3
    assert opciones["max_overflow"] == 4
    assert opciones["pool_timeout"] == 15
    assert opciones["pool_recycle"] == 120


def test_valores_invalidos_caen_a_los_defectos(monkeypatch):
    _limpiar(monkeypatch)
    monkeypatch.setenv("DB_POOL_SIZE", "abc")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "-3")

    opciones = db_supabase.opciones_pool()

    assert opciones["pool_size"] == 5
    assert opciones["max_overflow"] == 10


def test_serverless_no_retiene_conexiones(monkeypatch):
    _limpiar(monkeypatch)
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DB_POOL_SIZE", "5")

    opciones = db_supabase.opciones_pool()

    assert opciones["poolclass"] is NullPool
    assert "pool_size" not in opciones
    assert "max_overflow" not in opciones


def test_crear_engine_respeta_el_entorno_sin_conectar(monkeypatch):
    _limpiar(monkeypatch)
    monkeypatch.setenv("DATABASE_URI_TIENDA",
                       "postgresql+psycopg2://u:p@localhost:5432/tienda")

    engine_local = db_supabase._crear_engine("DATABASE_URI_TIENDA")
    monkeypatch.setenv("VERCEL", "1")
    engine_serverless = db_supabase._crear_engine("DATABASE_URI_TIENDA")

    try:
        assert isinstance(engine_local.pool, QueuePool)
        assert isinstance(engine_serverless.pool, NullPool)
    finally:
        engine_local.dispose()
        engine_serverless.dispose()
