"""Captcha aritmético propio para el registro de clientes.

Sin servicios externos: una suma o resta de dos números del 1 al 9
(el resultado nunca es negativo).

La sesión de Flask es una cookie firmada pero LEGIBLE, así que la
respuesta JAMÁS se guarda en texto plano. En la sesión solo quedan:

  * ``mac``:    HMAC-SHA256(SECRET_KEY, nonce + ':' + respuesta)
  * ``nonce``:  valor aleatorio que se mezcla en el MAC; sin él, la
                respuesta de un desafío podría reinyectarse copiando
                el MAC de otro captcha con la misma respuesta.
  * ``expira``: marca de tiempo Unix de vencimiento (10 minutos).

``generar()`` devuelve el texto de la pregunta para el formulario y
reemplaza cualquier captcha anterior de la sesión. ``validar()``
consume el captcha SIEMPRE (un solo uso), incluso si la respuesta es
incorrecta: con solo 17 respuestas posibles, permitir reintentos
contra el mismo desafío lo haría trivialemente vulnerable.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, session

CLAVE_SESION = 'captcha_registro'
MINUTOS_VIDA = 10


def _mac(nonce, respuesta):
    """HMAC-SHA256 con la SECRET_KEY de la app sobre nonce + respuesta."""
    clave = current_app.secret_key
    if isinstance(clave, str):
        clave = clave.encode('utf-8')
    return hmac.new(clave, f"{nonce}:{respuesta}".encode('utf-8'),
                    hashlib.sha256).hexdigest()


def generar():
    """Crea la operación, guarda solo su HMAC y devuelve la pregunta.

    La resta se ordena (mayor - menor) para que el resultado nunca sea
    negativo. Cada llamada invalida el captcha anterior de la sesión.
    """
    a = secrets.randbelow(9) + 1
    b = secrets.randbelow(9) + 1
    if secrets.randbelow(2):
        a, b = max(a, b), min(a, b)          # resta sin negativos
        pregunta, respuesta = f"¿Cuánto es {a} - {b}?", a - b
    else:
        pregunta, respuesta = f"¿Cuánto es {a} + {b}?", a + b
    nonce  = secrets.token_hex(16)
    expira = datetime.now(timezone.utc) + timedelta(minutes=MINUTOS_VIDA)
    session[CLAVE_SESION] = {
        'mac':    _mac(nonce, respuesta),
        'nonce':  nonce,
        'expira': expira.timestamp(),
    }
    return pregunta


def validar(respuesta):
    """Comprueba la respuesta y consume el captcha (un solo uso).

    Acepta espacios alrededor del número. Rechaza valores no
    numéricos, desafíos expirados y desafíos ya usados (o inexistentes).
    La comparación usa ``hmac.compare_digest`` para no filtrar por
    tiempos de ejecución.
    """
    datos = session.pop(CLAVE_SESION, None)   # se consume use o no use
    if not datos:
        return False
    try:
        expira = float(datos.get('expira') or 0)
    except (TypeError, ValueError):
        return False
    if datetime.now(timezone.utc).timestamp() >= expira:
        return False
    bruto = str(respuesta or '').strip()
    if not bruto.isdigit():
        return False
    return hmac.compare_digest(_mac(str(datos.get('nonce') or ''), int(bruto)),
                                str(datos.get('mac') or ''))
