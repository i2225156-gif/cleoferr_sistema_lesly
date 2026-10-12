"""B1: /pedidos/<id>/estado exige token CSRF.

Sin token o con token incorrecto la capa CSRF responde 400. Con token válido
pasa la capa CSRF y login_required redirige (302) antes de tocar la BD.
"""
from app import app


def test_post_sin_token_csrf_es_400():
    c = app.test_client()
    r = c.post('/pedidos/1/estado', data={'estado': 'pendiente'})
    assert r.status_code == 400


def test_post_con_token_invalido_es_400():
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_csrf_token'] = 'token-test'
    r = c.post('/pedidos/1/estado', data={'estado': 'pendiente', 'csrf_token': 'otro'})
    assert r.status_code == 400


def test_post_con_token_valido_pasa_csrf_y_redirige_a_login():
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_csrf_token'] = 'token-test'
    r = c.post('/pedidos/1/estado', data={'estado': 'pendiente', 'csrf_token': 'token-test'})
    assert r.status_code in (301, 302)
