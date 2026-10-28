"""Cabeceras de seguridad + cookies de sesión (sin BD).

Usa la ruta pública /privacidad (solo render del template): la BD nunca
se toca. Comprueba que TODAS las respuestas (página, 302 y 404) llevan
X-Content-Type-Options: nosniff, X-Frame-Options: SAMEORIGIN y
Referrer-Policy: strict-origin-when-cross-origin, y que NO aparece
Content-Security-Policy (rompería los scripts en línea y las CDN).

Además valida la configuración de cookies de sesión: HttpOnly y
SameSite=Lax siempre; SESSION_COOKIE_SECURE solo puede ser True en
despliegue (variable VERCEL) — en local debe quedar False o el login
dejaría de funcionar sobre HTTP.
"""
import os

import app as modulo_app
from app import app

CABECERAS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'SAMEORIGIN',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
}


def test_privacidad_lleva_las_tres_cabeceras():
    resp = app.test_client().get('/privacidad')
    assert resp.status_code == 200
    for nombre, valor in CABECERAS.items():
        assert resp.headers.get(nombre) == valor, (
            f"Falta o difiere la cabecera {nombre} en /privacidad")


def test_cabeceras_tambien_en_redirecciones_y_404():
    cliente = app.test_client()
    # '/' es 302 (redirige al catálogo) y '/no_existe' es 404 (error.html):
    # after_request debe aplicarse a TODAS las respuestas.
    for ruta in ('/', '/no_existe'):
        resp = cliente.get(ruta)
        for nombre, valor in CABECERAS.items():
            assert resp.headers.get(nombre) == valor, (
                f"Falta o difiere la cabecera {nombre} en {ruta} "
                f"(status {resp.status_code})")


def test_no_hay_content_security_policy():
    # Decisión del alcance: CSP rompería los scripts en línea y las CDN,
    # por lo que NINGUNA respuesta debe incluirla.
    cliente = app.test_client()
    for ruta in ('/privacidad', '/', '/no_existe'):
        resp = cliente.get(ruta)
        assert 'Content-Security-Policy' not in resp.headers, (
            f"CSP inesperada en {ruta}")


def test_cookie_sesion_httponly_y_samesite_lax():
    assert app.config.get('SESSION_COOKIE_HTTPONLY') is True
    assert app.config.get('SESSION_COOKIE_SAMESITE') == 'Lax'


def test_session_cookie_secure_solo_en_vercel():
    # Igualdad con el entorno real: bool(VERCEL). En local (sin la
    # variable) la configuración DEBE ser False.
    assert app.config.get('SESSION_COOKIE_SECURE') is bool(os.environ.get('VERCEL'))
