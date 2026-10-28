"""FASE 2 — numero()/entero() rechazan valores NO finitos (sin BD).

Decimal convierte 'NaN', 'sNaN', 'Infinity' y '-Infinity' SIN lanzar
InvalidOperation, así que numero() tenía tres comportamientos erróneos:
'Infinity' pasaba como número válido, '-Infinity' caía en el mensaje de
negativo y 'NaN'/'sNaN' reventaban con InvalidOperation al compararlos
con el mínimo (500). Ahora is_finite() se verifica ANTES de comparar y
los cuatro se rechazan con el mismo mensaje de «debe ser un número».

entero() ya los rechazaba porque int() no los admite (ValueError), pero
ese rechazo era incidental: ahora es explícito y a prueba de que alguien
sustituya int() por un parser que sí los acepte.

Solo se ejercen las funciones puras de services.validacion: sin Flask,
sin BD y sin fixtures de rollback.
"""
from decimal import Decimal

import pytest

from services.validacion import entero, numero

NO_FINITOS = ('NaN', 'Infinity', '-Infinity', 'sNaN')
GRAFIAS    = ('nan', 'inf', '-inf', '+Infinity')


# ── numero(): no finitos ─────────────────────────────────────

@pytest.mark.parametrize('valor', NO_FINITOS)
def test_numero_rechaza_no_finitos(valor):
    """Antes: 'Infinity' se aceptaba, '-Infinity' daba «negativo» y
    'NaN'/'sNaN' lanzaban InvalidOperation al comparar (500)."""
    assert numero('precio', valor) == (None, 'El campo «precio» debe ser un número.')


@pytest.mark.parametrize('valor', GRAFIAS)
def test_numero_rechaza_otras_grafias(valor):
    """Decimal admite los no finitos en cualquier grafía y con signo."""
    assert numero('precio', valor) == (None, 'El campo «precio» debe ser un número.')


# ── numero(): finitos siguen igual ───────────────────────────

def test_numero_valido_con_decimales():
    valor, error = numero('precio', ' 10.50 ')
    assert error is None
    assert valor == Decimal('10.50')


def test_numero_negativo_rechazado():
    assert numero('precio', '-1') == (None, 'El campo «precio» no puede ser negativo.')


def test_numero_no_numerico_rechazado():
    assert numero('precio', 'abc') == (None, 'El campo «precio» debe ser un número.')


def test_numero_vacio_obligatorio():
    assert numero('precio', '   ') == (None, 'El campo «precio» es obligatorio.')


# ── entero(): no finitos ─────────────────────────────────────

@pytest.mark.parametrize('valor', NO_FINITOS)
def test_entero_rechaza_no_finitos(valor):
    """int() no admite estas grafías (ValueError → mismo mensaje);
    el rechazo explícito evita depender de ese detalle."""
    assert entero('stock', valor) == (None, 'El campo «stock» debe ser un número entero.')


@pytest.mark.parametrize('valor', GRAFIAS)
def test_entero_rechaza_otras_grafias(valor):
    assert entero('stock', valor) == (None, 'El campo «stock» debe ser un número entero.')


# ── entero(): enteros siguen igual ───────────────────────────

def test_entero_valido():
    valor, error = entero('stock', ' 12 ')
    assert error is None
    assert valor == 12


def test_entero_con_decimales_rechazado():
    assert entero('stock', '5.5') == (None, 'El campo «stock» debe ser un número entero.')


def test_entero_negativo_rechazado():
    assert entero('stock', '-3') == (None, 'El campo «stock» no puede ser negativo.')
