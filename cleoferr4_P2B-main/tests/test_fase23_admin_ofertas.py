"""Fase 2.3 (pieza 4): panel admin de ofertas (/admin/ofertas).

Contra la BD real dentro de transacciones con rollback: nada persiste.
Producto sintético (S/ 9.99) y ofertas sintéticas. Verifica: listado con
estados (vigente/futura/vencida/inactiva), alta con CSRF y auditoría,
solapamiento rechazado con mensaje traducido (ex_oferta_vigencia),
edición con valor anterior y nuevo auditados, activar/desactivar, y el
trigger de precio_fijo (a nivel BD).
"""
import json
import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pytest

import db_supabase
import app as modulo_app
from app import app
from services.ofertas import calcular_descuento_unitario

_LIMA = timezone(timedelta(hours=-5))


class ConnProxy:
    def __init__(self, raw):
        self._raw = raw

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            return self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self._raw.cursor()

    def commit(self):
        pass

    def rollback(self):
        self._raw.rollback()

    def close(self):
        pass


def _sesion_admin():
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario_id'] = 8
        s['rol'] = 'administrador'
        s['_csrf_token'] = 'token-test'
    return c


def _f(dt):
    return dt.strftime('%Y-%m-%dT%H:%M')


@pytest.fixture()
def fx(monkeypatch):
    raw = db_supabase.engine_tienda.raw_connection()
    proxy = ConnProxy(raw)
    cur = proxy.cursor(dictionary=True)
    cur.execute(
        "INSERT INTO producto (nombre, precio, stock) VALUES (%s, 9.99, 50)"
        " RETURNING id_producto", (f"test23a-{_uuid.uuid4().hex[:10]}",))
    id_producto = cur.fetchone()['id_producto']
    monkeypatch.setattr(modulo_app, 'get_connection_tienda', lambda: proxy)
    yield {'client': _sesion_admin(), 'cur': cur, 'proxy': proxy,
           'id_producto': id_producto}
    proxy.rollback()
    raw.close()


def _form(fx, **kwargs):
    ahora = datetime.now(_LIMA)
    datos = {'id_producto': str(fx['id_producto']), 'tipo': 'porcentaje',
             'valor': '15', 'csrf_token': 'token-test',
             'fecha_inicio': _f(ahora - timedelta(hours=1)),
             'fecha_fin': _f(ahora + timedelta(days=1))}
    datos.update(kwargs)
    return datos


def _oferta_bd(fx, tipo='porcentaje', valor=15,
              inicio="NOW() - INTERVAL '1 hour'", fin="NOW() + INTERVAL '1 day'",
              activa=True):
    """Inserta una oferta sintética; inicio/fin son expresiones SQL completas."""
    fx['cur'].execute(f"""
        INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio, fecha_fin, activa)
        VALUES (%s, %s, %s, {inicio}, {fin}, %s)
        RETURNING id_oferta
    """, (fx['id_producto'], tipo, valor, activa))
    return fx['cur'].fetchone()['id_oferta']


def _auditoria_de(fx, id_oferta, accion):
    fx['cur'].execute("""SELECT * FROM auditoria
                          WHERE tabla_afectada = 'oferta' AND id_registro = %s
                            AND accion = %s
                          ORDER BY id_auditoria DESC LIMIT 1""",
                     (id_oferta, accion))
    return fx['cur'].fetchone()


def _n_ofertas(fx):
    fx['cur'].execute("SELECT COUNT(*) AS n FROM oferta WHERE id_producto = %s",
                     (fx['id_producto'],))
    return fx['cur'].fetchone()['n']


# ── Listado con estados ───────────────────────────────────────────

def test_listado_muestra_los_cuatro_estados(fx):
    cur = fx['cur']
    _oferta_bd(fx, valor=10)                                                     # vigente
    _oferta_bd(fx, valor=20, inicio="NOW() + INTERVAL '2 days'",
               fin="NOW() + INTERVAL '3 days'")                                  # futura
    _oferta_bd(fx, valor=30, inicio="NOW() - INTERVAL '3 days'",
               fin="NOW() - INTERVAL '1 day'")                                  # vencida
    _oferta_bd(fx, valor=40, activa=False)                                       # inactiva
    r = fx['client'].get('/admin/ofertas')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for estado in ('Vigente', 'Futura', 'Vencida', 'Inactiva'):
        assert estado in html


# ── Alta ──────────────────────────────────────────────────────────

def test_alta_crea_oferta_y_audita(fx):
    r = fx['client'].post('/admin/ofertas/nueva', data=_form(fx),
                          follow_redirects=True)
    assert r.status_code == 200
    cur = fx['cur']
    cur.execute("SELECT * FROM oferta WHERE id_producto = %s", (fx['id_producto'],))
    o = cur.fetchone()
    assert o['tipo'] == 'porcentaje'
    assert Decimal(str(o['valor'])) == Decimal('15')
    assert o['activa'] is True
    assert o['creado_por'] == 8                     # quien creó (de sesión)
    aud = _auditoria_de(fx, o['id_oferta'], 'crear')
    assert aud is not None
    assert aud['valor_anterior'] is None            # alta: sin valor anterior
    nuevo = json.loads(aud['valor_nuevo'])
    assert nuevo['tipo'] == 'porcentaje'
    assert nuevo['id_producto'] == fx['id_producto']
    html = r.get_data(as_text=True)
    assert 'creada correctamente' in html


def test_alta_sin_csrf_rechazada(fx):
    antes = _n_ofertas(fx)
    datos = _form(fx)
    datos.pop('csrf_token')
    r = fx['client'].post('/admin/ofertas/nueva', data=datos)
    assert r.status_code == 400
    assert _n_ofertas(fx) == antes                 # nada se creó


def test_alta_solapada_rechazada_con_mensaje_claro(fx):
    """La ruta traduce la ExclusionViolation y NO deja la oferta creada.

    OJO: al fallar, la ruta hace rollback de SU transaccion, que es la
    misma del fixture (conexion inyectada): los datos sinteticos del
    fixture tambien se revierten. Por eso se verifica que la solapante
    (valor 20) no quede en la transaccion — si la ruta la hubiera
    creado, seguiria visible en el mismo cursor.
    """
    _oferta_bd(fx, valor=10)                       # activa vigente
    r = fx['client'].post('/admin/ofertas/nueva', data=_form(fx, valor='20'),
                         follow_redirects=True)
    assert r.status_code == 200
    fx['cur'].execute("SELECT COUNT(*) AS n FROM oferta"
                      " WHERE id_producto = %s AND valor = 20",
                      (fx['id_producto'],))
    assert fx['cur'].fetchone()['n'] == 0          # la solapada NO quedó creada
    assert 'solapa' in r.get_data(as_text=True)    # ExclusionViolation traducida


def test_bd_exclusion_impide_dos_activas_solapadas(fx):
    """Red de seguridad a nivel BD: dos ACTIVAS solapantes no enturan; las
    de rangos disjuntos sí."""
    _oferta_bd(fx, valor=10)                      # activa vigente
    _oferta_bd(fx, valor=5, inicio="NOW() + INTERVAL '2 days'",
               fin="NOW() + INTERVAL '3 days'")   # futura disjunta: permitida
    with pytest.raises(psycopg2.errors.ExclusionViolation):
        _oferta_bd(fx, valor=20)                  # solapa la vigente: rechazada


def test_alta_precio_fijo_igual_al_precio_rechazada(fx):
    antes = _n_ofertas(fx)
    r = fx['client'].post('/admin/ofertas/nueva',
                          data=_form(fx, tipo='precio_fijo', valor='9.99'),
                          follow_redirects=True)
    assert r.status_code == 200
    assert _n_ofertas(fx) == antes                 # rechazo amigable, sin fila
    assert 'precio fijo' in r.get_data(as_text=True)


def test_alta_precio_fijo_valido(fx):
    fx['client'].post('/admin/ofertas/nueva',
                      data=_form(fx, tipo='precio_fijo', valor='7.50'),
                      follow_redirects=True)
    fx['cur'].execute("SELECT tipo, valor FROM oferta WHERE id_producto = %s",
                      (fx['id_producto'],))
    o = fx['cur'].fetchone()
    assert o is not None and o['tipo'] == 'precio_fijo'
    # El descuento que congelará el checkout: 9.99 - 7.50 = 2.49
    assert calcular_descuento_unitario(Decimal('9.99'), 'precio_fijo',
                                       o['valor']) == Decimal('2.49')


def test_bd_trigger_impide_precio_fijo_mayor(fx):
    """Red de seguridad: aunque el form fallara, el trigger de la BD impide
    persistir un precio_fijo >= precio del producto."""
    with pytest.raises(Exception) as ei:
        fx['cur'].execute(
            "INSERT INTO oferta (id_producto, tipo, valor, fecha_fin)"
            " VALUES (%s, 'precio_fijo', 15.00, NOW() + INTERVAL '1 day')",
            (fx['id_producto'],))
    assert 'oferta invalida' in str(ei.value)


# ── Edición con auditoría anterior/nuevo ──────────────────────────

def test_editar_cambia_valor_y_audita_anterior_y_nuevo(fx):
    id_of = _oferta_bd(fx, valor=15)
    r = fx['client'].post(f'/admin/ofertas/editar/{id_of}',
                          data=_form(fx, valor='25'), follow_redirects=True)
    assert r.status_code == 200
    fx['cur'].execute("SELECT valor FROM oferta WHERE id_oferta = %s", (id_of,))
    assert Decimal(str(fx['cur'].fetchone()['valor'])) == Decimal('25')
    aud = _auditoria_de(fx, id_of, 'editar')
    assert aud is not None
    anterior = json.loads(aud['valor_anterior'])
    nuevo = json.loads(aud['valor_nuevo'])
    assert anterior['valor'] == '15.00'            # valor ANTERIOR auditado
    assert nuevo['valor'] == '25.00'               # valor NUEVO auditado
    assert 'actualizada correctamente' in r.get_data(as_text=True)


def test_editar_ida_y_vuelta_sin_deriva_de_hora(fx):
    """El display de inicio/fin es hora Lima y el modal de edición
    precarga exactamente lo guardado: editar sin tocar las fechas no
    desplaza la vigencia. Con el display legado (+5h) cada ciclo
    editar-guardar corría la vigencia 5 horas."""
    base = datetime.now(_LIMA).replace(hour=8, minute=0, second=0,
                                       microsecond=0)
    fin = base + timedelta(days=1, hours=10)      # mañana 18:00 Lima
    cur = fx['cur']
    cur.execute("""
        INSERT INTO oferta (id_producto, tipo, valor, fecha_inicio,
                            fecha_fin, activa)
        VALUES (%s, 'porcentaje', 15, %s, %s, TRUE)
        RETURNING id_oferta
    """, (fx['id_producto'], base, fin))
    id_of = cur.fetchone()['id_oferta']

    html = fx['client'].get('/admin/ofertas').get_data(as_text=True)
    m = re.search(rf'data-id="{id_of}"[^>]*data-inicio="([^"]+)"'
                  r'[^>]*data-fin="([^"]+)"', html)
    assert m, 'el botón editar debe llevar data-inicio/data-fin'
    assert m.group(1) == _f(base)                 # el modal precarga hora Lima
    assert m.group(2) == _f(fin)
    assert base.strftime('%d/%m/%Y %H:%M') in html   # la tabla muestra igual

    # Ida y vuelta: POST de edición con lo que el modal precarga, tal
    # cual lo haría el admin que solo cambia el valor del descuento
    r = fx['client'].post(f'/admin/ofertas/editar/{id_of}',
                          data={'id_producto': str(fx['id_producto']),
                                'tipo': 'porcentaje', 'valor': '20',
                                'csrf_token': 'token-test',
                                'fecha_inicio': m.group(1),
                                'fecha_fin': m.group(2)},
                          follow_redirects=True)
    assert r.status_code == 200
    cur.execute("SELECT fecha_inicio, fecha_fin FROM oferta WHERE id_oferta = %s",
                (id_of,))
    fila = cur.fetchone()
    assert fila['fecha_inicio'] == base           # SIN deriva: mismo instante
    assert fila['fecha_fin'] == fin


# ── Activar / desactivar ──────────────────────────────────────────

def _venta_con_oferta(fx, id_oferta):
    """Venta sintética que usó la oferta (descuento congelado en detalle)."""
    fx['cur'].execute("""
        INSERT INTO venta (id_tipo_venta, id_estado_venta, num_operacion)
        VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre='online'),
                (SELECT id_estado_venta FROM estado_venta WHERE nombre='pendiente'),
                %s)
        RETURNING id_venta
    """, (f"test23a-{_uuid.uuid4().hex[:12]}",))
    id_venta = fx['cur'].fetchone()['id_venta']
    fx['cur'].execute("""
        INSERT INTO detalle_venta
            (id_venta, id_producto, cantidad, precio_unitario,
             id_oferta, descuento_unitario)
        VALUES (%s, %s, 1, 9.99, %s, 1.50)
    """, (id_venta, fx['id_producto'], id_oferta))
    return id_venta


def test_editar_oferta_usada_avisa_que_ventas_no_cambian(fx):
    id_of = _oferta_bd(fx, valor=15)
    _venta_con_oferta(fx, id_of)
    r = fx['client'].post(f'/admin/ofertas/editar/{id_of}',
                          data=_form(fx, valor='25'), follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'actualizada correctamente' in html
    assert 'ya hechas no cambian' in html     # aviso de ventas congeladas


def test_desactivar_oferta_usada_avisa_que_ventas_no_cambian(fx):
    id_of = _oferta_bd(fx, valor=15)
    _venta_con_oferta(fx, id_of)
    r = fx['client'].post(f'/admin/ofertas/desactivar/{id_of}',
                          data={'csrf_token': 'token-test'}, follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'desactivada correctamente' in html
    assert 'ya hechas no cambian' in html


def test_editar_oferta_no_usada_sin_aviso(fx):
    id_of = _oferta_bd(fx, valor=15)
    r = fx['client'].post(f'/admin/ofertas/editar/{id_of}',
                          data=_form(fx, valor='25'), follow_redirects=True)
    assert r.status_code == 200
    assert 'ya hechas no cambian' not in r.get_data(as_text=True)


def test_desactivar_y_activar_con_auditoria(fx):
    id_of = _oferta_bd(fx, valor=15)
    r = fx['client'].post(f'/admin/ofertas/desactivar/{id_of}',
                          data={'csrf_token': 'token-test'}, follow_redirects=True)
    assert r.status_code == 200
    fx['cur'].execute("SELECT activa FROM oferta WHERE id_oferta = %s", (id_of,))
    assert fx['cur'].fetchone()['activa'] is False
    aud = _auditoria_de(fx, id_of, 'desactivar')
    assert aud is not None and json.loads(aud['valor_anterior'])['activa'] is True
    assert json.loads(aud['valor_nuevo'])['activa'] is False

    fx['client'].post(f'/admin/ofertas/activar/{id_of}',
                      data={'csrf_token': 'token-test'}, follow_redirects=True)
    fx['cur'].execute("SELECT activa FROM oferta WHERE id_oferta = %s", (id_of,))
    assert fx['cur'].fetchone()['activa'] is True
    aud = _auditoria_de(fx, id_of, 'activar')
    assert aud is not None and json.loads(aud['valor_nuevo'])['activa'] is True


def test_activar_solapada_rechazada(fx):
    """Activar una oferta cuyo rango solapa otra ACTIVA: la BD lo impide y
    el mensaje se traduce. (El rollback de la ruta revierte tambien los
    datos del fixture: el rechazo se prueba por el mensaje traducido y por
    el test de BD de abajo.)"""
    _oferta_bd(fx, valor=10)                        # activa vigente
    id_b = _oferta_bd(fx, valor=20, activa=False)   # inactiva solapante
    r = fx['client'].post(f'/admin/ofertas/activar/{id_b}',
                          data={'csrf_token': 'token-test'}, follow_redirects=True)
    assert r.status_code == 200
    assert 'solapa' in r.get_data(as_text=True)


def test_bd_activar_solapante_rechazada(fx):
    """A nivel BD: UPDATE a activa de una solapante dispara la exclusión."""
    _oferta_bd(fx, valor=10)                        # activa vigente
    id_b = _oferta_bd(fx, valor=20, activa=False)   # inactiva solapante
    with pytest.raises(psycopg2.errors.ExclusionViolation):
        fx['cur'].execute("UPDATE oferta SET activa = TRUE WHERE id_oferta = %s",
                          (id_b,))
