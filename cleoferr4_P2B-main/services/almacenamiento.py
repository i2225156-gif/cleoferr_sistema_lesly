"""Subida de imágenes de producto a Supabase Storage.

En Vercel el disco de la función es de solo lectura, así que las imágenes
nuevas se guardan en un bucket público de Supabase. Este módulo usa solo la
biblioteca estándar (urllib) para no añadir dependencias.

Variables de entorno:
- ``SUPABASE_TIENDA_PROJECT_URL``: URL base del proyecto de la tienda.
- ``SUPABASE_TIENDA_SECRET_KEY``: clave de servicio; NUNCA se registra.
- ``SUPABASE_BUCKET_PRODUCTOS``: bucket (por defecto ``productos``).
"""

import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

URL_ENV = 'SUPABASE_TIENDA_PROJECT_URL'
CLAVE_ENV = 'SUPABASE_TIENDA_SECRET_KEY'
BUCKET_ENV = 'SUPABASE_BUCKET_PRODUCTOS'
BUCKET_POR_DEFECTO = 'productos'
TIMEOUT_SEGUNDOS = 10


def _configuracion():
    """Devuelve (url_base, clave, bucket) o None si falta algo."""
    url_base = (os.environ.get(URL_ENV) or '').strip().rstrip('/')
    clave = (os.environ.get(CLAVE_ENV) or '').strip()
    if not url_base or not clave:
        return None
    bucket = ((os.environ.get(BUCKET_ENV) or '').strip()
              or BUCKET_POR_DEFECTO)
    return url_base, clave, bucket


def configurado():
    """True solo si hay URL y clave de servicio de tienda."""
    return _configuracion() is not None


def _url_publica(url_base, bucket, nombre):
    return (f"{url_base}/storage/v1/object/public/"
            f"{urllib.parse.quote(bucket, safe='')}/"
            f"{urllib.parse.quote(nombre, safe='/')}")


def subir(contenido, nombre, tipo_mime):
    """Sube ``contenido`` al bucket y devuelve su URL pública o None.

    Cualquier fallo (falta de configuración, red o HTTP) devuelve None y se
    registra sin incluir la clave de servicio ni datos personales.
    """
    config = _configuracion()
    if config is None or not contenido or not nombre:
        return None
    url_base, clave, bucket = config

    destino = (f"{url_base}/storage/v1/object/"
               f"{urllib.parse.quote(bucket, safe='')}/"
               f"{urllib.parse.quote(nombre, safe='/')}")
    peticion = urllib.request.Request(
        destino,
        data=contenido,
        method='POST',
        headers={
            'apikey': clave,
            'Authorization': f'Bearer {clave}',
            'Content-Type': tipo_mime or 'application/octet-stream',
        },
    )

    try:
        with urllib.request.urlopen(peticion,
                                    timeout=TIMEOUT_SEGUNDOS) as respuesta:
            estado = getattr(respuesta, 'status', 200)
            if not 200 <= estado < 300:
                logger.warning(
                    "Storage rechazó la subida de imagen (HTTP %s).", estado)
                return None
    except urllib.error.HTTPError as error:
        logger.warning(
            "Storage rechazó la subida de imagen (HTTP %s).", error.code)
        return None
    except (urllib.error.URLError, OSError, ValueError) as error:
        logger.warning("No se pudo subir la imagen a Storage (%s).",
                       type(error).__name__)
        return None
    except Exception as error:  # nunca romper el alta del producto
        logger.warning("Error inesperado al subir la imagen a Storage (%s).",
                       type(error).__name__)
        return None

    return _url_publica(url_base, bucket, nombre)
