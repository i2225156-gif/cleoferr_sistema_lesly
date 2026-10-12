"""Validación y normalización de documentos de identidad (cliente).

Tipos soportados (buscados por NOMBRE en el catálogo tipo_documento de
la BD AUTH, nunca por id):
    DNI        8 dígitos
    RUC        11 dígitos, prefijo 10/20, dígito verificador módulo 11
    CE         9 caracteres alfanuméricos (FORMATO A VERIFICAR)
    Pasaporte  6-12 caracteres alfanuméricos
    Otro       sin validación de formato

El número se NORMALIZA antes de guardar: sin espacios, MAYÚSCULAS.
"""
import re
from decimal import Decimal

from .sunat.simulado import ruc_valido

# Longitud máxima de la columna nro_documento en AUTH (VARCHAR(20)).
MAX_LONGITUD = 20

_RE_DNI = re.compile(r'^\d{8}$')
_RE_RUC = re.compile(r'^\d{11}$')
_RE_CE = re.compile(r'^[A-Z0-9]{9}$')       # A VERIFICAR con el contador
_RE_PASAPORTE = re.compile(r'^[A-Z0-9]{6,12}$')

# Nombre canónico de cada tipo (LOWER(TRIM(nombre)) en la BD).
NOMBRES_TIPO = ('dni', 'ruc', 'ce', 'pasaporte', 'otro')


def normalizar_numero(numero):
    """Sin espacios, mayúsculas, máx 20 caracteres."""
    if not numero:
        return ''
    return re.sub(r'\s+', '', str(numero)).upper()[:MAX_LONGITUD]


def validar_documento(tipo_nombre, numero):
    """Valida el par (tipo, número) y devuelve (ok, mensaje).

    'tipo_nombre' es el nombre CANÓNICO en minúsculas ('dni', 'ruc',
    'ce', 'pasaporte', 'otro'). El número debe estar YA normalizado
    (llama normalizar_numero antes).
    """
    if tipo_nombre not in NOMBRES_TIPO:
        return False, f'Tipo de documento desconocido: "{tipo_nombre}".'
    if not numero:
        return False, 'El número de documento es obligatorio.'
    if len(numero) > MAX_LONGITUD:
        return False, f'El número de documento no puede superar {MAX_LONGITUD} caracteres.'
    if tipo_nombre == 'dni':
        if not _RE_DNI.match(numero):
            return False, 'El DNI debe tener exactamente 8 dígitos.'
    elif tipo_nombre == 'ruc':
        if not _RE_RUC.match(numero):
            return False, 'El RUC debe tener exactamente 11 dígitos.'
        if not ruc_valido(numero):
            return False, 'El RUC no tiene un dígito verificador válido (módulo 11).'
    elif tipo_nombre == 'ce':
        # FORMATO A VERIFICAR: 9 caracteres alfanuméricos es una suposición
        # razonable; no está confirmado con la normativa de Migraciones.
        if not _RE_CE.match(numero):
            return False, 'El Carné de Extranjería debe tener 9 caracteres alfanuméricos (formato a verificar).'
    elif tipo_nombre == 'pasaporte':
        if not _RE_PASAPORTE.match(numero):
            return False, 'El pasaporte debe tener entre 6 y 12 caracteres alfanuméricos.'
    # 'otro': sin validación de formato específica
    return True, ''


def tipo_permite_factura(tipo_nombre):
    """True si el tipo de documento del cliente habilita FACTURA."""
    return tipo_nombre == 'ruc'


def tipo_documento_de_cliente(cursor, id_cliente):
    """Lee el documento del cliente desde AUTH y devuelve un dict neutral.

    Devuelve {'tipo': nombre_canonico|None, 'numero': str|None} o None
    si el cliente no existe. SIN datos personales más allá del propio
    documento (que es el propósito de la consulta).
    """
    cursor.execute("""
        SELECT LOWER(TRIM(td.nombre)) AS tipo, c.nro_documento
          FROM cliente c
          LEFT JOIN tipo_documento td ON td.id_tipo_documento = c.id_tipo_documento
         WHERE c.id_cliente = %s
    """, (id_cliente,))
    fila = cursor.fetchone()
    if not fila:
        return None
    tipo = fila['tipo'] if isinstance(fila, dict) else fila[0]
    numero = fila['nro_documento'] if isinstance(fila, dict) else fila[1]
    numero = normalizar_numero(numero) if numero else None
    return {'tipo': tipo if tipo else None, 'numero': numero if numero else None}
