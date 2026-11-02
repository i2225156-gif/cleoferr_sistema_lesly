"""Formulario de proveedor: consulta RUC desde la plantilla (sin BD).

Renderiza /proveedores/nuevo (GET no toca la BD) y verifica que la
plantilla incluya los IDs del formulario, el patrón de lectura de la
respuesta de /api/consultar_ruc (razonSocial/razon_social/nombre y
direccion/domicilioFiscal), el autocomplete completo sin dividir, el
aviso amarillo de no ACTIVO/HABIDO y la auto-consulta una sola vez por
número.
"""
import pytest

import app as modulo_app


@pytest.fixture
def html_proveedor():
    """HTML renderizado de /proveedores/nuevo (GET, sin BD)."""
    with modulo_app.app.test_client() as c:
        with c.session_transaction() as s:
            s['usuario_id'] = 1
            s['rol'] = 'administrador'
        resp = c.get('/proveedores/nuevo')
        assert resp.status_code == 200
        return resp.get_data(as_text=True)


def test_formulario_tiene_los_ids(html_proveedor):
    for id_ in ('tipoDocProv', 'numDocProv', 'estadoDocProv',
                'btnConsultarRucProv', 'spinProv', 'iconoBtnProv',
                'cajResProv', 'contResProv',
                'campoNombreProv', 'campoDireccionProv'):
        assert f'id="{id_}"' in html_proveedor


def test_boton_consultar_con_listener_y_endpoint(html_proveedor):
    assert 'id="btnConsultarRucProv"' in html_proveedor
    assert html_proveedor.count('/api/consultar_ruc/') >= 1
    assert "btnRucProv.addEventListener('click', consultarRucProv)" in html_proveedor


def test_lectura_respuesta_patron_cliente_form(html_proveedor):
    """Mismos nombres de campo que cliente_form.html en todos sus formas."""
    assert 'data.razonSocial || data.razon_social || data.nombre' in html_proveedor
    assert 'data.direccion   || data.domicilioFiscal' in html_proveedor


def test_autocomplete_razon_social_completa(html_proveedor):
    """Nombre empresa con la razón social COMPLETA (sin split) y Dirección."""
    assert "campoNombreProv').value = razon" in html_proveedor
    assert "campoDireccionProv').value = direccion" in html_proveedor
    # Sin división tipo nombre/apellido (patrón de cliente) en este script
    assert 'setNombreApellido' not in html_proveedor


def test_muestra_estado_condicion_y_aviso_amarillo(html_proveedor):
    assert "RUC ENCONTRADO" in html_proveedor
    assert '(data.estado' in html_proveedor
    assert "(data.condicion" in html_proveedor
    assert "'ACTIVO'" in html_proveedor
    assert "'HABIDO'" in html_proveedor
    assert 'aviso-warn' in html_proveedor          # caja amarilla
    assert 'data.error || data.message' in html_proveedor


def test_autoconsulta_una_sola_vez_por_numero(html_proveedor):
    assert 'numDocProv.addEventListener(\'input\', validarDocProv)' in html_proveedor
    assert 'consultarRucProv();' in html_proveedor          # disparo automático
    assert 'provUltimoRuc' in html_proveedor
    assert 'provUltimoRuc = num' in html_proveedor          # una vez por número
