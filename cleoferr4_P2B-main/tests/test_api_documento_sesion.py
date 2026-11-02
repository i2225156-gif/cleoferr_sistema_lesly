"""Las APIs de consulta RUC/DNI exigen sesión (protege el token de ApisPerú).

Sin sesión -> JSON {'ok': False, 'error': 'Inicia sesión para validar el
documento.'} con 401 (no login_required, que redirigiría a HTML y rompería
el fetch). Sin BD y sin llamar a ApisPerú: se monkeyparchea
app._consultar_api_sunat para contar/falsificar su invocación.
"""
import pytest

import app as modulo_app


@pytest.fixture
def cliente_api(monkeypatch):
    """test_client con SpaceX... con _consultar_api_sunat falsificado."""
    grabador = {'llamadas': []}

    def falsa_api(tipo, numero):
        grabador['llamadas'].append((tipo, numero))
        if tipo == 'ruc':
            return ({'razonSocial': 'PROVEEDOR SIMULADO SAC',
                     'direccion': 'AV. SIMULADA 123'}, 200, None)
        return ({'nombres': 'JUAN', 'apellidoPaterno': 'PRUEBA'}, 200, None)

    monkeypatch.setattr(modulo_app, '_consultar_api_sunat', falsa_api)
    with modulo_app.app.test_client() as c:
        yield c, grabador


def _iniciar_sesion(cliente):
    with cliente.session_transaction() as s:
        s['usuario_id'] = 1
        s['rol'] = 'administrador'


def test_ruc_sin_sesion_401(cliente_api):
    c, grab = cliente_api
    resp = c.get('/api/consultar_ruc/20123456786')
    assert resp.status_code == 401
    data = resp.get_json()
    assert data['ok'] is False
    assert data['error'] == 'Inicia sesión para validar el documento.'
    assert grab['llamadas'] == []                    # nunca tocó ApisPerú


def test_dni_sin_sesion_401(cliente_api):
    c, grab = cliente_api
    resp = c.get('/api/consultar_dni/12345678')
    assert resp.status_code == 401
    data = resp.get_json()
    assert data['ok'] is False
    assert data['error'] == 'Inicia sesión para validar el documento.'
    assert grab['llamadas'] == []


def test_ruc_con_sesion_flujo_completo(cliente_api):
    c, grab = cliente_api
    _iniciar_sesion(c)
    resp = c.get('/api/consultar_ruc/20123456786')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['razonSocial'] == 'PROVEEDOR SIMULADO SAC'
    assert grab['llamadas'] == [('ruc', '20123456786')]


def test_dni_con_sesion_flujo_completo(cliente_api):
    c, grab = cliente_api
    _iniciar_sesion(c)
    resp = c.get('/api/consultar_dni/12345678')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert 'JUAN' in (data.get('nombres') or data.get('nombre') or '')
    assert grab['llamadas'] == [('dni', '12345678')]


def test_con_sesion_ruc_invalido_sigue_400(cliente_api):
    """Con sesión, la validación previa de formato se conserva (400)."""
    c, grab = cliente_api
    _iniciar_sesion(c)
    resp = c.get('/api/consultar_ruc/123')
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False
    assert grab['llamadas'] == []
    resp = c.get('/api/consultar_dni/123')
    assert resp.status_code == 400
    assert grab['llamadas'] == []
