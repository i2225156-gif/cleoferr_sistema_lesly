"""Validaciones de servidor compartidas (FASE 2).

Reglas comunes:
- Trim SIEMPRE; el texto vacío o de solo espacios se rechaza cuando el
  campo es requerido.
- Números no válidos o negativos se rechazan con mensaje claro (nunca
  un 500 de la BD).
- Nombres de PERSONAS: letras con tildes y ñ, espacios, apóstrofo y
  guion; jamás dígitos.
- Nombres de PRODUCTO y razón social de PROVEEDOR PUEDEN contener
  dígitos ('Taladro 650W'): solo se rechaza vacío, solo espacios y
  longitud excesiva.
- Correo: formato válido y en minúsculas.

Todas las funciones devuelven (valor_limpio, None) si todo queda bien o
(None, mensaje_para_el_usuario) si falla: el mensaje finaliza en punto y
no expone detalles técnicos de la BD.
"""
import re
from decimal import Decimal, InvalidOperation

_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Letras (con tildes, ñ, ü), espacios, apóstrofo y guion. Sin dígitos.
_NOMBRE_PERSONA = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' \-]+$")


def texto(valor):
    """Trim del valor; None si queda vacío o son solo espacios."""
    limpio = (valor or '').strip()
    return limpio if limpio else None


def requerido(nombre_campo, valor, maximo=None):
    """Texto requerido tras trim, con longitud máxima opcional."""
    limpio = texto(valor)
    if limpio is None:
        return None, f'El campo «{nombre_campo}» es obligatorio.'
    if maximo and len(limpio) > maximo:
        return None, (f'El campo «{nombre_campo}» no puede superar '
                      f'{maximo} caracteres.')
    return limpio, None


def nombre_persona(nombre_campo, valor, maximo=None):
    """Nombre/apellido de PERSONA: letras con tildes y ñ, espacios,
    apóstrofo y guion. Los dígitos se rechazan."""
    limpio, error = requerido(nombre_campo, valor, maximo)
    if error:
        return None, error
    if not _NOMBRE_PERSONA.match(limpio):
        return None, (f'El campo «{nombre_campo}» solo admite letras '
                      f'(con tildes y ñ), espacios, apóstrofo y guion.')
    return limpio, None


def correo(valor, maximo=None):
    """Correo requerido con formato válido, en minúsculas."""
    limpio, error = requerido('correo electrónico', valor, maximo)
    if error:
        return None, error
    if not _CORREO.match(limpio):
        return None, 'El correo electrónico no tiene un formato válido.'
    return limpio.lower(), None


def numero(nombre_campo, valor, minimo=0):
    """Decimal requerido; no numérico o por debajo del mínimo se rechaza."""
    limpio = texto(valor)
    if limpio is None:
        return None, f'El campo «{nombre_campo}» es obligatorio.'
    try:
        n = Decimal(limpio)
    except InvalidOperation:
        return None, f'El campo «{nombre_campo}» debe ser un número.'
    if n < minimo:
        return None, f'El campo «{nombre_campo}» no puede ser negativo.'
    return n, None


def entero(nombre_campo, valor, minimo=0):
    """Entero requerido; no entero o negativo se rechaza con mensaje claro."""
    limpio = texto(valor)
    if limpio is None:
        return None, f'El campo «{nombre_campo}» es obligatorio.'
    try:
        n = int(limpio)
    except ValueError:
        return None, f'El campo «{nombre_campo}» debe ser un número entero.'
    if n < minimo:
        return None, f'El campo «{nombre_campo}» no puede ser negativo.'
    return n, None
