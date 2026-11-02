"""Gestión de usuarios y permisos del personal (RF13/RF26) — SOLO ADMIN.

La BD de AUTENTICACIÓN se usa en modo SINTÉTICO: los usuarios de prueba
se insertan dentro de una transacción que NUNCA se confirma (el commit de
la app se intercepta y, al final, el test hace rollback). Nada queda
confirmado en la base real: ni usuarios, ni permisos, ni estados.

Cobertura:
  * el administrador ve la pantalla de personal (correo, rol, estado,
    intentos fallidos y las casillas del catálogo);
  * vendedor (302 / 403 AJAX) y cliente (302) NO entran;
  * quitar un permiso a un vendedor sintético cierra su módulo (302 a
    /inicio_personal) y darlo de nuevo lo abre;
  * una casilla fuera del catálogo se rechaza (400) sin tocar nada;
  * los módulos solo se editan en cuentas de vendedor;
  * nadie se desactiva a sí mismo ni deja el sistema sin administrador;
  * sin CSRF la escritura se rechaza (400);
  * desbloquear limpia intentos fallidos y bloqueo;
  * el log de auditoría solo lleva ids (nunca correos).
"""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import pytest

import app as modulo_app
from app import app


TOKEN = 'csrf-admin-usuarios'


class ConnPrueba:
    """Conexión de la app que NUNCA confirma ni revierte la transacción.

    La app usa ESTA conexión (parcheada) para ver a los usuarios
    sintéticos y sus permisos; commit() y rollback() no hacen nada: el
    test revierte todo al final.
    """

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            return self._conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor)
        return self._conn.cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class CursorTienda:
    """Doble mínimo de la BD TIENDA (la vista solo lee)."""

    def __init__(self):
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return {'total': 0, 'cnt': 0}


class ConnTienda:
    def __init__(self):
        self.cur = CursorTienda()

    def cursor(self, dictionary=False, **kwargs):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def bd(monkeypatch):
    """Transacción sintética: la app ve los datos de prueba, no se guardan."""
    uri = os.environ.get('DATABASE_URI_AUTH')
    if not uri:
        pytest.skip('DATABASE_URI_AUTH no configurada')
    try:
        conn = psycopg2.connect(uri)
    except Exception as exc:
        pytest.skip('BD de autenticación no disponible: %s' % exc)
    monkeypatch.setattr(modulo_app, 'get_connection_auth',
                        lambda: ConnPrueba(conn))
    monkeypatch.setattr(modulo_app, 'get_connection_tienda',
                        lambda: ConnTienda())
    yield conn
    conn.rollback()
    conn.close()


def _crear_usuario(conn, rol, activo=True, intentos=0, bloqueado=False):
    """Inserta un usuario SINTÉTICO (se revierte al final del test)."""
    cur = conn.cursor()
    cur.execute('SELECT id_rol FROM rol WHERE LOWER(TRIM(nombre)) = %s',
                (rol,))
    id_rol = cur.fetchone()[0]
    email = 'qa_%s_%s@cleoferr.local' % (rol, uuid.uuid4().hex[:10])
    hasta = (datetime.now(timezone.utc) + timedelta(minutes=10)
             if bloqueado else None)
    cur.execute("""
        INSERT INTO usuario (email, contrasena, id_rol, nombres, activo,
                             intentos_fallidos, bloqueado_hasta)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id_usuario
    """, (email, 'sintetico', id_rol, 'Usuario Sintetico', activo,
          intentos, hasta))
    id_usuario = cur.fetchone()[0]
    cur.close()
    return id_usuario, email


def _sesion(cliente, uid, rol):
    with cliente.session_transaction() as s:
        s['usuario_id'] = uid
        s['rol'] = rol
        s['nombre'] = 'Personal Sintetico'
        s['_csrf_token'] = TOKEN


def _activo(conn, id_usuario):
    cur = conn.cursor()
    cur.execute('SELECT activo FROM usuario WHERE id_usuario = %s',
                (id_usuario,))
    valor = cur.fetchone()[0]
    cur.close()
    return valor


def _modulos(conn, id_usuario):
    cur = conn.cursor()
    cur.execute("""SELECT p.nombre FROM usuario_permiso up
                     JOIN permiso p ON p.id_permiso = up.id_permiso
                    WHERE up.id_usuario = %s""", (id_usuario,))
    nombres = sorted(fila[0] for fila in cur.fetchall())
    cur.close()
    return nombres


# ── 1. La pantalla (solo administrador) ───────────────────────────
def test_admin_ve_la_pantalla_de_usuarios(bd):
    id_vendedor, email = _crear_usuario(bd, 'vendedor')
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    r = admin.get('/admin/usuarios')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert email in html                      # correo del personal
    assert 'Vendedor' in html                 # rol
    assert 'Intentos fallidos' in html        # contador de intentos
    assert 'value="ventas"' in html           # casilla del catálogo
    # Los módulos SOLO-ADMIN no aparecen como casillas
    for clave in ('dashboard', 'pagos_simulados', 'reservas', 'usuarios'):
        assert 'value="%s"' % clave not in html, clave
    # Ítem del sidebar solo para administrador
    assert 'href="/admin/usuarios"' in html


def test_vendedor_no_entra_a_la_pantalla(bd):
    id_vendedor, _ = _crear_usuario(bd, 'vendedor')
    vendedor = app.test_client()
    _sesion(vendedor, id_vendedor, 'vendedor')
    r = vendedor.get('/admin/usuarios', follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/inicio_personal')
    ajax = vendedor.get('/admin/usuarios',
                         headers={'X-Requested-With': 'XMLHttpRequest'})
    assert ajax.status_code == 403
    assert ajax.get_json()['ok'] is False


def test_cliente_no_entra_a_la_pantalla(bd):
    cliente = app.test_client()
    with cliente.session_transaction() as s:
        s['usuario_id'] = 999999
        s['rol'] = 'cliente'
        s['nombre'] = 'Cliente Sintetico'
    r = cliente.get('/admin/usuarios', follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/inicio_personal')


# ── 2. Permisos por módulo ────────────────────────────────────────
def test_quitar_permiso_cierra_el_modulo_y_darlo_lo_reabre(bd):
    id_vendedor, _ = _crear_usuario(bd, 'vendedor')
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')

    r = admin.post('/admin/usuarios/%d/permisos' % id_vendedor,
                   data={'csrf_token': TOKEN, 'permisos': ['productos']},
                   follow_redirects=False)
    assert r.status_code == 302
    assert _modulos(bd, id_vendedor) == ['productos']

    vendedor = app.test_client()
    _sesion(vendedor, id_vendedor, 'vendedor')
    assert vendedor.get('/productos').status_code == 200

    # Quitar el módulo: la siguiente petición ya no entra
    r = admin.post('/admin/usuarios/%d/permisos' % id_vendedor,
                   data={'csrf_token': TOKEN}, follow_redirects=False)
    assert r.status_code == 302
    assert _modulos(bd, id_vendedor) == []
    r = vendedor.get('/productos', follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/inicio_personal')

    # Volver a darlo: el módulo se abre otra vez
    r = admin.post('/admin/usuarios/%d/permisos' % id_vendedor,
                   data={'csrf_token': TOKEN, 'permisos': ['productos']},
                   follow_redirects=False)
    assert r.status_code == 302
    assert vendedor.get('/productos').status_code == 200


def test_casilla_fuera_del_catalogo_se_rechaza(bd):
    id_vendedor, _ = _crear_usuario(bd, 'vendedor')
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    r = admin.post('/admin/usuarios/%d/permisos' % id_vendedor,
                   data={'csrf_token': TOKEN,
                         'permisos': ['productos', 'clave_inventada']},
                   follow_redirects=False)
    assert r.status_code == 400
    assert _modulos(bd, id_vendedor) == []      # no se tocó nada


def test_modulos_solo_se_editan_para_vendedores(bd):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    otro_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    r = admin.post('/admin/usuarios/%d/permisos' % otro_admin,
                   data={'csrf_token': TOKEN, 'permisos': ['ventas']},
                   follow_redirects=False)
    assert r.status_code == 302
    assert _modulos(bd, otro_admin) == []


# ── 3. Estado de la cuenta ────────────────────────────────────────
def test_no_se_puede_desactivar_a_si_mismo(bd):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    r = admin.post('/admin/usuarios/%d/estado' % id_admin,
                   data={'csrf_token': TOKEN, 'accion': 'desactivar'},
                   follow_redirects=False)
    assert r.status_code == 302
    assert _activo(bd, id_admin) is True


def test_no_se_puede_dejar_el_sistema_sin_administrador(bd):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    # Se apagan (sin confirmar) los demás administradores reales
    cur = bd.cursor()
    cur.execute("""UPDATE usuario SET activo = FALSE
                    WHERE id_usuario <> %s
                      AND id_rol = (SELECT id_rol FROM rol
                                     WHERE LOWER(TRIM(nombre)) = 'administrador')""",
                (id_admin,))
    cur.close()
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    r = admin.post('/admin/usuarios/%d/estado' % id_admin,
                   data={'csrf_token': TOKEN, 'accion': 'desactivar'},
                   follow_redirects=False)
    assert r.status_code == 302
    assert _activo(bd, id_admin) is True


def test_desactivar_y_activar_un_vendedor(bd):
    id_vendedor, _ = _crear_usuario(bd, 'vendedor')
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    r = admin.post('/admin/usuarios/%d/estado' % id_vendedor,
                   data={'csrf_token': TOKEN, 'accion': 'desactivar'},
                   follow_redirects=False)
    assert r.status_code == 302
    assert _activo(bd, id_vendedor) is False
    r = admin.post('/admin/usuarios/%d/estado' % id_vendedor,
                   data={'csrf_token': TOKEN, 'accion': 'activar'},
                   follow_redirects=False)
    assert r.status_code == 302
    assert _activo(bd, id_vendedor) is True


def test_desbloquear_limpia_intentos_y_bloqueo(bd):
    id_vendedor, _ = _crear_usuario(bd, 'vendedor', intentos=3,
                                     bloqueado=True)
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    r = admin.post('/admin/usuarios/%d/desbloquear' % id_vendedor,
                   data={'csrf_token': TOKEN}, follow_redirects=False)
    assert r.status_code == 302
    cur = bd.cursor()
    cur.execute('SELECT intentos_fallidos, bloqueado_hasta FROM usuario'
                ' WHERE id_usuario = %s', (id_vendedor,))
    intentos, hasta = cur.fetchone()
    cur.close()
    assert intentos == 0
    assert hasta is None


# ── 4. CSRF ───────────────────────────────────────────────────────
def test_csrf_faltante_da_400(bd):
    id_vendedor, _ = _crear_usuario(bd, 'vendedor')
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    assert admin.post('/admin/usuarios/%d/permisos' % id_vendedor,
                      data={'permisos': ['productos']}).status_code == 400
    assert admin.post('/admin/usuarios/%d/estado' % id_vendedor,
                      data={'accion': 'desactivar'}).status_code == 400
    assert admin.post('/admin/usuarios/%d/desbloquear' % id_vendedor,
                      data={}).status_code == 400


# ── 5. Auditoría en el log (solo ids) ────────────────────────────
def test_log_de_auditoria_solo_con_ids(bd, caplog):
    id_vendedor, email = _crear_usuario(bd, 'vendedor')
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    with caplog.at_level(logging.INFO):
        admin.post('/admin/usuarios/%d/permisos' % id_vendedor,
                   data={'csrf_token': TOKEN, 'permisos': ['ventas']},
                   follow_redirects=False)
    assert 'usuario_id=%d' % id_vendedor in caplog.text
    assert email not in caplog.text


# ── 6. Alta de vendedor desde el panel ───────────────────────────
CLAVE_NUEVA = 'ClaveVendedor123'


def _usuario_por_correo(conn, correo):
    cur = conn.cursor()
    cur.execute("""SELECT u.id_usuario, u.contrasena, u.activo,
                          LOWER(TRIM(r.nombre))
                     FROM usuario u
                     JOIN rol r ON r.id_rol = u.id_rol
                    WHERE LOWER(TRIM(u.email)) = %s""",
                (correo.strip().lower(),))
    fila = cur.fetchone()
    cur.close()
    return fila


def _plantilla_vendedor(conn):
    cur = conn.cursor()
    cur.execute("""SELECT p.nombre
                     FROM rol_permiso rp
                     JOIN rol r ON r.id_rol = rp.id_rol
                     JOIN permiso p ON p.id_permiso = rp.id_permiso
                    WHERE LOWER(TRIM(r.nombre)) = 'vendedor'
                      AND p.activo = TRUE""")
    nombres = sorted(fila[0] for fila in cur.fetchall())
    cur.close()
    return nombres


def _permisos_asignados(conn, id_usuario):
    cur = conn.cursor()
    cur.execute("""SELECT p.nombre, up.asignado_por
                     FROM usuario_permiso up
                     JOIN permiso p ON p.id_permiso = up.id_permiso
                    WHERE up.id_usuario = %s""", (id_usuario,))
    filas = cur.fetchall()
    cur.close()
    return {fila[0]: fila[1] for fila in filas}


def _correo_nuevo():
    return 'nuevo_%s@cleoferr.local' % uuid.uuid4().hex[:10]


def test_admin_crea_vendedor_con_plantilla_y_hash(bd):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    correo = _correo_nuevo()
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')

    r = admin.post('/admin/usuarios/nuevo',
                   data={'csrf_token': TOKEN, 'correo': correo,
                         'contrasena': CLAVE_NUEVA,
                         'confirmar': CLAVE_NUEVA},
                   follow_redirects=True)

    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Vendedor creado; sus módulos iniciales son:' in html
    assert CLAVE_NUEVA not in html

    fila = _usuario_por_correo(bd, correo)
    assert fila is not None
    id_nuevo, hash_pw, activo, rol = fila
    assert rol == 'vendedor'
    assert activo is True
    assert modulo_app.bcrypt.check_password_hash(hash_pw, CLAVE_NUEVA)

    plantilla = _plantilla_vendedor(bd)
    asignados = _permisos_asignados(bd, id_nuevo)
    assert sorted(asignados) == plantilla
    assert set(asignados.values()) == {id_admin}


def test_vendedor_nuevo_puede_iniciar_sesion(bd):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    correo = _correo_nuevo()
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    admin.post('/admin/usuarios/nuevo',
               data={'csrf_token': TOKEN, 'correo': correo,
                     'contrasena': CLAVE_NUEVA,
                     'confirmar': CLAVE_NUEVA})

    login = app.test_client()
    with login.session_transaction() as s:
        s['_csrf_token'] = TOKEN
    r = login.post('/login',
                   data={'csrf_token': TOKEN, 'correo': correo.upper(),
                         'clave': CLAVE_NUEVA},
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/inicio_personal')
    with login.session_transaction() as s:
        assert s['rol'] == 'vendedor'


def test_correo_duplicado_sin_distinguir_mayusculas(bd):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    correo = _correo_nuevo()
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')
    datos = {'csrf_token': TOKEN, 'correo': correo,
             'contrasena': CLAVE_NUEVA, 'confirmar': CLAVE_NUEVA}
    assert admin.post('/admin/usuarios/nuevo', data=datos).status_code == 302

    datos['correo'] = correo.upper()
    r = admin.post('/admin/usuarios/nuevo', data=datos,
                   follow_redirects=True)
    assert r.status_code == 200
    assert 'Ya existe una cuenta con ese correo electrónico.' in \
        r.get_data(as_text=True)

    cur = bd.cursor()
    cur.execute("""SELECT COUNT(1) FROM usuario
                    WHERE LOWER(TRIM(email)) = %s""", (correo.lower(),))
    total = cur.fetchone()[0]
    cur.close()
    assert total == 1


@pytest.mark.parametrize('correo, clave, confirmar', [
    ('sin-arroba', CLAVE_NUEVA, CLAVE_NUEVA),
    (_correo_nuevo(), '123', '123'),
    (_correo_nuevo(), CLAVE_NUEVA, 'OtraClave123'),
])
def test_creacion_invalida_no_crea_cuenta(bd, correo, clave, confirmar):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')

    r = admin.post('/admin/usuarios/nuevo',
                   data={'csrf_token': TOKEN, 'correo': correo,
                         'contrasena': clave, 'confirmar': confirmar},
                   follow_redirects=True)

    assert r.status_code == 200
    if '@' in correo:
        assert _usuario_por_correo(bd, correo) is None


def test_vendedor_no_puede_crear_usuarios(bd):
    id_vendedor, _ = _crear_usuario(bd, 'vendedor')
    vendedor = app.test_client()
    _sesion(vendedor, id_vendedor, 'vendedor')
    datos = {'csrf_token': TOKEN, 'correo': _correo_nuevo(),
             'contrasena': CLAVE_NUEVA, 'confirmar': CLAVE_NUEVA}

    r = vendedor.post('/admin/usuarios/nuevo', data=datos,
                      follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/inicio_personal')

    ajax = vendedor.post('/admin/usuarios/nuevo', data=datos,
                         headers={'X-Requested-With': 'XMLHttpRequest'},
                         follow_redirects=False)
    assert ajax.status_code == 403
    assert ajax.get_json()['ok'] is False


def test_alta_de_vendedor_sin_csrf_da_400(bd):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')

    r = admin.post('/admin/usuarios/nuevo',
                   data={'correo': _correo_nuevo(),
                         'contrasena': CLAVE_NUEVA,
                         'confirmar': CLAVE_NUEVA})
    assert r.status_code == 400


def test_alta_no_filtra_clave_ni_correo_en_logs(bd, caplog):
    id_admin, _ = _crear_usuario(bd, 'administrador')
    correo = _correo_nuevo()
    admin = app.test_client()
    _sesion(admin, id_admin, 'administrador')

    with caplog.at_level(logging.INFO):
        admin.post('/admin/usuarios/nuevo',
                   data={'csrf_token': TOKEN, 'correo': correo,
                         'contrasena': CLAVE_NUEVA,
                         'confirmar': CLAVE_NUEVA})

    assert CLAVE_NUEVA not in caplog.text
    assert correo not in caplog.text
