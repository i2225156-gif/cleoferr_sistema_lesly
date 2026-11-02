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

from .comprobantes import ruc_valido_para_factura

_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Letras (con tildes, ñ, ü), espacios, apóstrofo y guion. Sin dígitos.
_NOMBRE_PERSONA = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' \-]+$")
# Teléfono OPCIONAL: solo dígitos, entre 7 y 9 (se ignoran espacios).
_TELEFONO = re.compile(r"^\d{7,9}$")

# Documento de identidad del PROVEEDOR (db/12_proveedor_documento.sql,
# BD TIENDA). RUC con prefijo de tipo de contribuyente; CE = carné de
# extranjería, 9 a 12 caracteres (letras y números).
_TIPOS_DOC_PROVEEDOR = ('RUC', 'DNI', 'CE')
_RE_RUC = re.compile(r"^\d{11}$")
_RE_DNI = re.compile(r"^\d{8}$")
_RE_CE = re.compile(r"^[A-Za-z0-9]{9,12}$")
_PREFIJOS_RUC = ('10', '15', '17', '20')


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


def telefono(valor):
    """Teléfono OPCIONAL: sin espacios debe ser solo dígitos y de 7 a 9.
    Si llega vacío devuelve (None, None); si no cumple, el mensaje."""
    limpio = texto(valor)
    if limpio is None:
        return None, None
    sin_espacios = limpio.replace(' ', '')
    if not _TELEFONO.match(sin_espacios):
        return None, 'El teléfono solo debe contener números (7 a 9 dígitos).'
    return sin_espacios, None


def correo(valor, maximo=None):
    """Correo requerido con formato válido, en minúsculas."""
    limpio, error = requerido('correo electrónico', valor, maximo)
    if error:
        return None, error
    if not _CORREO.match(limpio):
        return None, 'El correo electrónico no tiene un formato válido.'
    return limpio.lower(), None


def documento_proveedor(tipo, numero):
    """Documento de identidad OBLIGATORIO del proveedor (RUC, DNI o CE).

    Devuelve (tipo_normalizado, numero_limpio, None) si el par es válido
    o (None, None, mensaje_para_el_usuario) si falla (mismo convenio del
    resto del módulo). El número se guarda sin espacios internos y el
    CE en MAYÚSCULAS (coherente con services.documentos).
    """
    tipo_limpio = (tipo or '').strip().upper()
    numero_limpio = re.sub(r'\s+', '', (numero or '').strip())
    if tipo_limpio not in _TIPOS_DOC_PROVEEDOR:
        return None, None, 'El tipo de documento debe ser RUC, DNI o CE.'
    if not numero_limpio:
        return None, None, 'El número de documento es obligatorio.'
    if tipo_limpio == 'RUC':
        if not _RE_RUC.match(numero_limpio) \
                or numero_limpio[:2] not in _PREFIJOS_RUC:
            return None, None, (f'El RUC no es válido (11 dígitos, '
                                f'empieza con 10, 15, 17 o 20).')
        if numero_limpio[:2] in ('10', '20'):
            # Reutiliza el proveedor SUNAT: dígito verificador módulo 11.
            if not ruc_valido_para_factura(numero_limpio):
                return None, None, ('El RUC no es válido '
                                    '(dígito verificador incorrecto).')
        else:
            # Prefijos 15/17 fuera del alcance del simulado: mismo
            # módulo 11 de SUNAT aplicado en servidor.
            if not _digito_verificador_ruc(numero_limpio):
                return None, None, ('El RUC no es válido '
                                    '(dígito verificador incorrecto).')
    elif tipo_limpio == 'DNI':
        if not _RE_DNI.match(numero_limpio):
            return None, None, 'El DNI debe tener exactamente 8 dígitos.'
    else:
        if not _RE_CE.match(numero_limpio):
            return None, None, ('El Carné de Extranjería debe tener entre '
                                '9 y 12 caracteres (solo letras y números).')
    return tipo_limpio, numero_limpio.upper(), None


def _digito_verificador_ruc(ruc):
    """Módulo 11 de SUNAT (pesos 5,4,3,2,7,6,5,4,3,2; 10->0, 11->1)."""
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    suma = sum(int(ruc[i]) * pesos[i] for i in range(10))
    digito = 11 - (suma % 11)
    if digito == 10:
        digito = 0
    elif digito == 11:
        digito = 1
    return digito == int(ruc[10])


def numero(nombre_campo, valor, minimo=0):
    """Decimal requerido; no numérico, no finito o por debajo del
    mínimo se rechaza."""
    limpio = texto(valor)
    if limpio is None:
        return None, f'El campo «{nombre_campo}» es obligatorio.'
    try:
        n = Decimal(limpio)
    except InvalidOperation:
        return None, f'El campo «{nombre_campo}» debe ser un número.'
    if not n.is_finite():
        return None, f'El campo «{nombre_campo}» debe ser un número.'
    if n < minimo:
        return None, f'El campo «{nombre_campo}» no puede ser negativo.'
    return n, None


def entero(nombre_campo, valor, minimo=0):
    """Entero requerido; no entero o negativo se rechaza con mensaje claro."""
    limpio = texto(valor)
    if limpio is None:
        return None, f'El campo «{nombre_campo}» es obligatorio.'
    if limpio.lower().lstrip('+-') in ('nan', 'snan', 'inf', 'infinity'):
        return None, f'El campo «{nombre_campo}» debe ser un número entero.'
    try:
        n = int(limpio)
    except ValueError:
        return None, f'El campo «{nombre_campo}» debe ser un número entero.'
    if n < minimo:
        return None, f'El campo «{nombre_campo}» no puede ser negativo.'
    return n, None
