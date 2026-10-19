import os
import sys
import uuid
import secrets
import hmac
import hashlib
import json
# A�'ADIR despuǸs de: import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode
# Intento seguro de importar dependencias externas; si faltan, mostrar instrucciones claras y salir.
try:
    from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
    from flask_bcrypt import Bcrypt
    from functools import wraps
    from werkzeug.utils import secure_filename   # ← NUEVO
    from dotenv import load_dotenv
except Exception as e:
    print("\nERROR: faltan dependencias necesarias para ejecutar la aplicación.")
    print("Instale las dependencias dentro del entorno virtual y vuelva a intentarlo.")
    print("Comandos recomendados (desde el venv activado):")
    print("  pip install -r requirements.txt")
    print("Si no dispone de requirements.txt, instale al menos:")
    print("  pip install flask flask-bcrypt psycopg2-binary sqlalchemy python-dotenv")
    print("\nDetalle del error:", e, "\n")
    sys.exit(1)

from db import db
from db_supabase import get_connection_auth, get_connection_tienda
from psycopg2 import errors as _pg_errores
from services.comprobantes import (EMISOR, UMBRAL_BOLETA_SIN_DOC,
                                    boleta_necesita_documento,
                                    ruc_valido_para_factura,
                                    siguiente_numero as _siguiente_numero_comprobante,
                                    texto_qr)
from services.documentos import (NOMBRES_TIPO, normalizar_numero,
                                 tipo_documento_de_cliente, validar_documento)
from services.horario import SQL_DIA_LIMA, SQL_HOY_LIMA, SQL_PARED_LIMA
from services.inventario import devolver_stock_venta
from services.ofertas import (SQL_IMPORTE_LINEA, SQL_PRECIO_VIGENTE,
                              calcular_descuento_unitario, oferta_vigente,
                              precios_vigentes)
from services.validacion import entero, numero, requerido, texto
from services.reservas import (liberar_reservas_vencidas, preview_reservas_vencidas,
                               total_reservas_vencidas)
from services.pagos import (cancelar_pedido_con_pago_inicial, confirmar_pago,
                            registrar_evento)
from services.pasarela import (EstadoPago, PagoResultado, PagoSolicitud,
                               crear_pasarela)
from sqlalchemy import text

load_dotenv()

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY no definida. Créala en el archivo .env (p. ej. con: "
        "python -c \"import secrets; print(secrets.token_hex(32))\")."
    )

os.environ['TZ'] = 'America/Lima'

# El ORM (modelo Producto) usa la BD Tienda.
_tienda_uri = os.environ.get("DATABASE_URI_TIENDA")
if not _tienda_uri:
    raise RuntimeError("DATABASE_URI_TIENDA no definida en el archivo .env.")
app.config["SQLALCHEMY_DATABASE_URI"] = _tienda_uri

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "pool_timeout": 30
}

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
UPLOAD_FOLDER      = os.path.join(app.root_path, 'static', 'img')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024   # 5 MB máximo
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def guardar_imagen(file_field):
    archivo = request.files.get(file_field)
    if not archivo or archivo.filename == '':
        return None
    if not allowed_file(archivo.filename):
        flash('Formato no permitido. Usa JPG, PNG, WEBP o GIF.', 'danger')
        return None
    ext          = archivo.filename.rsplit('.', 1)[1].lower()
    nombre_unico = f"{uuid.uuid4().hex}.{ext}"
    archivo.save(os.path.join(UPLOAD_FOLDER, nombre_unico))
    return nombre_unico
db.init_app(app)
bcrypt = Bcrypt(app)


def enviar_correo(destino, asunto, cuerpo_html, cuerpo_texto=None):
    """Envia un correo via Gmail SMTP.

    Intenta primero SSL en el puerto 465 y, si falla la conexion, STARTTLS en
    el 587 (muchas redes universitarias/ISP bloquean uno de los dos).

    Usa SMTP_USER / SMTP_PASS del entorno. SMTP_PASS debe ser una
    "contrasena de aplicacion" de Gmail de 16 caracteres, NO la clave normal.

    Retorna:
      - None                  -> envio exitoso
      - 'smtp_no_configurado' -> faltan SMTP_USER/SMTP_PASS en el .env
      - 'smtp_auth'           -> Gmail rechazo usuario/contrasena de aplicacion
      - 'smtp_conexion'       -> no se pudo conectar (red/firewall/puerto)
      - 'error'               -> cualquier otro fallo de envio
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr

    smtp_user = os.environ.get('SMTP_USER', '').strip()
    # Gmail muestra la clave de aplicacion como "abcd efgh ijkl mnop":
    # quitamos espacios para que el login no falle por un copiado con espacios.
    smtp_pass = os.environ.get('SMTP_PASS', '').replace(' ', '').strip()

    if not smtp_user or not smtp_pass or smtp_pass.startswith('ROTA_ESTA'):
        app.logger.error(
            "SMTP NO CONFIGURADO: define SMTP_USER y SMTP_PASS (contrasena de "
            "aplicacion de Gmail) en el archivo .env y reinicia la aplicacion."
        )
        return 'smtp_no_configurado'

    msg = MIMEMultipart('alternative')
    msg['Subject'] = asunto
    msg['From'] = formataddr(('Ferreteria CLEOFERR', smtp_user))
    msg['To'] = destino
    msg.attach(MIMEText(cuerpo_texto or '', 'plain', 'utf-8'))
    msg.attach(MIMEText(cuerpo_html, 'html', 'utf-8'))

    ultimo_error = None
    for modo, puerto in (('ssl', 465), ('starttls', 587)):
        server = None
        try:
            if modo == 'ssl':
                import ssl
                server = smtplib.SMTP_SSL('smtp.gmail.com', puerto, timeout=15,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP('smtp.gmail.com', puerto, timeout=15)
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [destino], msg.as_string())
            app.logger.info(f"Correo enviado a {destino} por {modo}:{puerto} ({asunto})")
            return None
        except smtplib.SMTPAuthenticationError as e:
            app.logger.error(
                "Gmail rechazo las credenciales SMTP. Revisa que SMTP_USER sea la "
                "cuenta completa y que SMTP_PASS sea una contrasena de APLICACION "
                f"(16 caracteres) con verificacion en 2 pasos activa. Detalle: {e}"
            )
            return 'smtp_auth'
        except Exception as e:
            ultimo_error = e
            app.logger.warning(f"Fallo envio por {modo}:{puerto} -> {e}")
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass

    app.logger.error(f"No se pudo enviar el correo a {destino}. Ultimo error: {ultimo_error}")
    return 'smtp_conexion'


def _normalizar_correo(valor):
    """Normaliza un correo: sin espacios y en minusculas.

    Evita que 'Juan@Gmail.com ' y 'juan@gmail.com' se traten como distintos,
    que es una de las causas de duplicados y de logins fallidos.
    """
    return (valor or '').strip().lower()


class Producto(db.Model):
    __tablename__ = "producto"
    id_producto  = db.Column(db.Integer, primary_key=True)
    nombre       = db.Column(db.String(100))
    descripcion  = db.Column(db.Text)
    precio       = db.Column(db.Numeric(10, 2))
    stock        = db.Column(db.Integer, default=0)
    id_categoria = db.Column(db.Integer)
    id_marca     = db.Column(db.Integer)
    activo       = db.Column(db.Boolean, default=True)
    imagen       = db.Column(db.String(255))

    def __repr__(self):
        return f"<Producto {self.nombre}>"


# ── Decorators y utilidades de sesión ───────────────────────
# ── CSRF focalizado ─────────────────────────────────────────
# Sin depender de Flask-WTF: token por sesión, validado SOLO en los endpoints
# de mayor riesgo (auth, OTP, cambio de clave, carrito, POS).
# (Integrado desde origin/Lesly; se quitaron de la lista las rutas muertas
#  procesar_pago y las que no existen en esta rama.)
_CSRF_PROTEGIDAS = {
    'login', 'login_cliente', 'registro_cliente', 'verificar_registro',
    'recuperar_contrasena', 'restablecer_contrasena', 'cambiar_clave',
    'carrito_confirmar_v2', 'carrito_agregar', 'carrito_precios',
    'registrar_pos',
    'pedido_cambiar_estado',
    'pago_simulado_procesar',
    'admin_pagos_disparar',
    'admin_reservas_liberar',
    'admin_ofertas_nueva', 'admin_ofertas_editar',
    'admin_ofertas_activar', 'admin_ofertas_desactivar',
    'perfil_actualizar_documento',
    # C3d: borrados destructivos convertidos de GET a POST con CSRF
    'eliminar_producto', 'eliminar_cliente',
    # Commit A: alta admin de clientes (form HTML con token en cliente_form)
    'nuevo_cliente',
}


def _obtener_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_hex(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def _ctx_csrf_token():
    return {'csrf_token': _obtener_csrf_token()}


def _es_ajax():
    """Petición AJAX: fetch del frontend (cabecera) o Accept: application/json."""
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in (request.headers.get('Accept') or ''))


@app.before_request
def _validar_csrf():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    if request.endpoint not in _CSRF_PROTEGIDAS:
        return None
    procedente = request.form.get('csrf_token') \
        or request.headers.get('X-CSRFToken') \
        or (request.get_json(silent=True) or {}).get('csrf_token')
    if procedente and hmac.compare_digest(str(procedente), session.get('_csrf_token', '')):
        return None
    if _es_ajax():
        # Contrato JSON para fetch: nunca una página HTML de error (bug
        # del desplegable de /pedidos: response.json() sobre <!doctype>).
        return {'ok': False,
                'mensaje': 'Token CSRF inválido o ausente. Recarga la página e inténtalo de nuevo.'}, 400
    return abort(400)


@app.context_processor
def _ctx_recuperacion_origen():
    """Determina desde qué login se llegó a recuperar_contrasena (admin | cliente)."""
    return {'recuperar_origen': request.values.get('origen', 'cliente')}


# ── Páginas de error sin detalles técnicos (FASE 2) ─────────────
@app.errorhandler(404)
def _error_404(e):
    return render_template('error.html', codigo=404,
                           mensaje='La página que buscas no existe o fue movida.'), 404


@app.errorhandler(500)
def _error_500(e):
    return render_template('error.html', codigo=500,
                           mensaje='Ocurrió un error inesperado y ya quedó '
                                   'registrado en el log. Inténtalo de nuevo '
                                   'en un momento.'), 500


# Rutas de cliente (públicas/catálogo) que deben redirigir a /login_cliente,
# no al login administrativo
_RUTAS_CLIENTE = ('/carrito', '/mis_pedidos')


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario_id" not in session:
            if _es_ajax():
                return {'ok': False,
                        'mensaje': 'Tu sesión expiró. Inicia sesión de nuevo.'}, 401
            # Si la petición viene de una ruta de cliente, mandar a login de clientes
            if any(request.path.startswith(r) for r in _RUTAS_CLIENTE):
                return redirect(url_for("login_cliente"))
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("rol") != "administrador":
            if _es_ajax():
                return {'ok': False,
                        'mensaje': 'No tienes permisos para esta acción.'}, 403
            flash("Acceso denegado.", "danger")
            return redirect(url_for("productos"))
        return f(*args, **kwargs)
    return decorated_function

def escritura_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("rol") not in ("administrador", "vendedor"):
            if _es_ajax():
                return {'ok': False,
                        'mensaje': 'No tienes permisos para esta acción.'}, 403
            flash("No tienes permisos para realizar esta acción.", "danger")
            return redirect(url_for("productos"))
        return f(*args, **kwargs)
    return decorated_function


# ── Auth ─────────────────────────────────────────────────────
@app.route('/')
def inicio():
    return redirect(url_for('catalogo_cliente'))


# ── Seguridad de login: bloqueo por intentos fallidos (1b) ────────
# Umbral y duración por variables de entorno (defaults: 5 intentos,
# 15 minutos). El contador y el bloqueo usan las columnas EXISTENTES de
# la BD Auth (intentos_fallidos, bloqueado_hasta). Las tablas
# intento_login e ip_bloqueada existen pero NO se usan (decisión: un
# bloqueo por IP encerraría aulas/oficinas enteras con IP compartida,
# incluida la demo; quedan intactas y sin crear nada).
def _login_intentos_max():
    try:
        return max(1, int(os.environ.get('LOGIN_INTENTOS_MAX', '5')))
    except ValueError:
        return 5


def _login_bloqueo_min():
    try:
        return max(1, int(os.environ.get('LOGIN_BLOQUEO_MIN', '15')))
    except ValueError:
        return 15


def _cuenta_bloqueada(fila):
    """True si la cuenta está bloqueada AHORA (columna bloqueado_hasta)."""
    from datetime import timezone
    hasta = fila.get('bloqueado_hasta')
    return bool(hasta and hasta > datetime.now(timezone.utc))


def _login_intento_fallido(conn, fila, es_cliente):
    """Cuenta un intento fallido; al llegar al umbral bloquea la cuenta.

    La auditoría del bloqueo va al log del servidor con el id de la
    cuenta, nunca el correo (regla de no datos personales en logs).
    """
    cursor = conn.cursor()
    if es_cliente:
        tabla, id_col, id_cuenta = 'cliente', 'id_cliente', fila['id_cliente']
    else:
        tabla, id_col, id_cuenta = 'usuario', 'id_usuario', fila['id_usuario']
    intentos = int(fila.get('intentos_fallidos') or 0) + 1
    if intentos >= _login_intentos_max():
        cursor.execute(f"""
            UPDATE {tabla}
               SET intentos_fallidos = %s,
                   bloqueado_hasta = NOW() + make_interval(mins => %s)
             WHERE {id_col} = %s
        """, (intentos, _login_bloqueo_min(), id_cuenta))
        app.logger.warning('Login bloqueado tras %d intentos fallidos: %s %s',
                           intentos, tabla, id_cuenta)
    else:
        cursor.execute(f"UPDATE {tabla} SET intentos_fallidos = %s"
                       f" WHERE {id_col} = %s", (intentos, id_cuenta))
    conn.commit()


def _login_reiniciar_intentos(conn, fila, es_cliente):
    """Acceso exitoso: contador a cero y bloqueo retirado."""
    cursor = conn.cursor()
    if es_cliente:
        cursor.execute("UPDATE cliente SET intentos_fallidos = 0,"
                       " bloqueado_hasta = NULL WHERE id_cliente = %s",
                       (fila['id_cliente'],))
    else:
        cursor.execute("UPDATE usuario SET intentos_fallidos = 0,"
                       " bloqueado_hasta = NULL WHERE id_usuario = %s",
                       (fila['id_usuario'],))
    conn.commit()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        correo = _normalizar_correo(request.form['correo'])
        clave  = request.form['clave']
        conn   = get_connection_auth()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT u.id_usuario, u.nombres, u.email, u.contrasena, r.nombre AS rol,
                   u.activo, u.intentos_fallidos, u.bloqueado_hasta
            FROM usuario u
            INNER JOIN rol r ON u.id_rol = r.id_rol
            WHERE LOWER(TRIM(u.email)) = %s
        """, (correo,))
        usuario = cursor.fetchone()

        def _invalido():
            return render_template('login.html',
                                   error='Credenciales incorrectas')

        try:
            # 1a: cuenta inactiva = credencial inválida, sin revelar el motivo
            if usuario and not usuario.get('activo'):
                usuario = None
            # 1b: bloqueada por intentos fallidos → mismo mensaje genérico
            if usuario and _cuenta_bloqueada(usuario):
                return _invalido()
            if not (usuario and bcrypt.check_password_hash(usuario['contrasena'], clave)):
                if usuario:
                    _login_intento_fallido(conn, usuario, es_cliente=False)
                return _invalido()
            # acceso exitoso: contador a cero
            _login_reiniciar_intentos(conn, usuario, es_cliente=False)
        finally:
            conn.close()
        rol_usuario = (usuario.get('rol') or '').lower()
        # Permitir solo administradores y vendedores en este login
        if rol_usuario not in ('administrador', 'vendedor'):
            # Mensaje breve indicando redirección al login de clientes
            return render_template('login.html', error='Acceso restringido. Si eres cliente usa el acceso de clientes: /login_cliente')
        session['usuario_id'] = usuario['id_usuario']
        session['rol']        = usuario['rol']
        session['nombre']     = usuario['nombres']
        return redirect(url_for('productos'))
    # Botón WhatsApp para recuperar credenciales (personal administrativo)
    msg_admin = quote(
        "Hola Soporte de Inversiones CLEOFERR, soy del personal ADMINISTRATIVO "
        "(Administradora/Vendedora) y presento problemas con mis credenciales de acceso al sistema."
    )
    url_whatsapp = f"https://wa.me/51900555015?text={msg_admin}"
    return render_template('login.html', url_whatsapp=url_whatsapp)


@app.route('/login_cliente', methods=['GET', 'POST'])
def login_cliente():
    if request.method == 'POST':
        correo     = _normalizar_correo(request.form['correo'])
        contrasena = request.form['contrasena']
        conn       = get_connection_auth()
        cursor     = conn.cursor(dictionary=True)
        # Busca el cliente por email — la columna nombre puede variar
        cursor.execute(
            "SELECT * FROM cliente WHERE LOWER(TRIM(email)) = %s ORDER BY id_cliente LIMIT 1",
            (correo,))
        cliente = cursor.fetchone()

        def _invalido():
            return render_template('login_cliente.html',
                                   error='Credenciales incorrectas')

        try:
            # 1a: cuenta inactiva o sin verificar = credencial inválida,
            # sin revelar el motivo (el registro web activa con el OTP;
            # desde aquí no se distingue 'inactiva' de 'sin verificar')
            if cliente and not cliente.get('activo'):
                cliente = None
            # 1b: bloqueada por intentos fallidos → mismo mensaje genérico
            if cliente and _cuenta_bloqueada(cliente):
                return _invalido()
            if cliente:
                # Solo Bcrypt: las contraseñas de clientes deben estar
                # hasheadas con bcrypt; si no, el acceso se rechaza.
                stored = cliente.get('contrasena') or cliente.get('password') or ''
                try:
                    ok = bcrypt.check_password_hash(stored, contrasena)
                except (ValueError, TypeError):
                    ok = False
                if ok:
                    # acceso exitoso: contador a cero
                    _login_reiniciar_intentos(conn, cliente, es_cliente=True)
                    nombre_cliente = (
                        cliente.get('nombre') or
                        cliente.get('nombres') or
                        cliente.get('name') or
                        cliente.get('email')
                    )
                    session['usuario_id'] = cliente.get('id_cliente') or cliente.get('id')
                    session['rol']        = 'cliente'
                    session['nombre']     = nombre_cliente
                    return redirect(url_for('catalogo_cliente'))
                _login_intento_fallido(conn, cliente, es_cliente=True)
            return _invalido()
        finally:
            conn.close()
    # Botón WhatsApp para recuperar credenciales (clientes)
    msg_cliente = quote(
        "Hola Soporte de Inversiones CLEOFERR, soy un CLIENTE registrado y necesito ayuda "
        "para recuperar mi contraseña o mis datos de acceso al sistema web."
    )
    url_whatsapp = f"https://wa.me/51900555015?text={msg_cliente}"
    return render_template('login_cliente.html', url_whatsapp=url_whatsapp)


@app.route('/logout')
@login_required
def logout():
    es_cliente = session.get('rol') == 'cliente'
    session.clear()
    return redirect(url_for('login_cliente') if es_cliente else url_for('login'))



@app.route('/productos')
@login_required
def productos():
    if session.get('rol') == 'cliente':
        return redirect(url_for('catalogo_cliente'))

    categoria = request.args.get('categoria')
    marca     = request.args.get('marca')
    conn      = get_connection_tienda()
    cursor    = conn.cursor(dictionary=True)

    # P2: filtros por nombre de categoría/marca sobre el conjunto completo
    filtros_sql = ""
    params = []
    if categoria:
        filtros_sql += " AND c.nombre = %s"
        params.append(categoria)
    if marca:
        filtros_sql += " AND m.nombre = %s"
        params.append(marca)
    from_sql = (" FROM producto p"
                " LEFT JOIN categoria c ON p.id_categoria = c.id_categoria"
                " LEFT JOIN marca m ON p.id_marca = m.id_marca"
                " WHERE 1=1" + filtros_sql)
    # COUNT del conjunto FILTRADO completo (no de la página)
    cursor.execute("SELECT COUNT(*) AS total" + from_sql, params)
    total = cursor.fetchone()['total']
    pagina = _pagina_actual(total, POR_PAGINA_ADMIN)
    cursor.execute("SELECT p.*, c.nombre AS categoria, m.nombre AS marca"
                   + from_sql + " ORDER BY p.id_producto LIMIT %s OFFSET %s",
                   params + [POR_PAGINA_ADMIN, (pagina - 1) * POR_PAGINA_ADMIN])
    lista = cursor.fetchall()

    cursor.execute("SELECT * FROM categoria")
    categorias = cursor.fetchall()
    cursor.execute("SELECT * FROM marca")
    marcas = cursor.fetchall()

    # Contadores para los cards del dashboard
    pedidos_count = 0
    alertas_count = 0
    movimientos_count = 0
    bajo_stock = []
    try:
        cursor.execute("SELECT COUNT(*) AS cnt FROM venta")
        pedidos_count = cursor.fetchone()['cnt']
    except Exception:
        app.logger.exception("Error contando ventas en dashboard de productos")
    try:
        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM alerta 
            WHERE resuelta = FALSE AND id_tipo_alerta = 1 AND id_producto IN (SELECT id_producto FROM producto WHERE stock < 15)
        """)
        alertas_count = cursor.fetchone()['cnt']
    except Exception:
        app.logger.exception("Error contando alertas en dashboard de productos")
    try:
        cursor.execute(f"SELECT COUNT(*) AS cnt FROM inventario_movimiento WHERE {SQL_DIA_LIMA.format(p='', col='fecha')} = {SQL_HOY_LIMA}")
        movimientos_count = cursor.fetchone()['cnt']
    except Exception:
        app.logger.exception("Error contando movimientos de inventario del día en dashboard de productos")
    try:
        cursor.execute("""
            SELECT id_producto, nombre, stock, stock_minimo, precio
            FROM producto WHERE stock < 15 ORDER BY stock ASC
        """)
        bajo_stock = cursor.fetchall()
    except Exception:
        app.logger.exception("Error consultando productos con bajo stock en dashboard de productos")

    conn.close()

    return render_template('productos.html', productos=lista, categorias=categorias, marcas=marcas,
                           pedidos_count=pedidos_count, alertas_count=alertas_count,
                           reportes_count=movimientos_count, bajo_stock=bajo_stock,
                           pagina=pagina, total=total, por_pagina=POR_PAGINA_ADMIN,
                           filtros_qs=_filtros_sin_pagina())


@app.route('/productos/detalle/<int:id>')
@login_required
@escritura_required
def producto_detalle(id):
    producto = db.get_or_404(Producto, id)
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    movimientos = []
    categoria_nombre = '–'
    marca_nombre = '–'
    try:
        if producto.id_categoria:
            cursor.execute("SELECT nombre FROM categoria WHERE id_categoria = %s", (producto.id_categoria,))
            row = cursor.fetchone()
            if row: categoria_nombre = row['nombre']
        if producto.id_marca:
            cursor.execute("SELECT nombre FROM marca WHERE id_marca = %s", (producto.id_marca,))
            row = cursor.fetchone()
            if row: marca_nombre = row['nombre']
        cursor.execute("""
            SELECT im.*, p.nombre AS producto_nombre
            FROM inventario_movimiento im
            LEFT JOIN producto p ON im.id_producto = p.id_producto
            WHERE im.id_producto = %s
            ORDER BY im.fecha DESC LIMIT 20
        """, (id,))
        movimientos = cursor.fetchall()
    except Exception:
        app.logger.exception("Error consultando detalle/movimientos del producto %s", id)
    finally:
        conn.close()
    return render_template('producto_detalle.html', producto=producto, movimientos=movimientos,
                           categoria_nombre=categoria_nombre, marca_nombre=marca_nombre)


@app.route('/reportes/pedidos/exportar')
@login_required
@escritura_required
def exportar_pedidos_excel():
    """Exporta todos los pedidos a un archivo Excel."""
    try:
        import io, csv
        from flask import make_response
        # Consulta 1 (Tienda): ventas con total, sin joins cross-BD
        conn   = get_connection_tienda()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"""
            SELECT v.id_venta,
                   {SQL_PARED_LIMA.format(p='v.', col='fecha')} AS fecha,
                   v.id_cliente,
                   ev.nombre AS estado,
                   tv.nombre AS tipo_venta,
                   v.id_usuario_vendedor,
                   COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
            FROM venta v
            LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
            LEFT JOIN tipo_venta tv ON v.id_tipo_venta = tv.id_tipo_venta
            LEFT JOIN detalle_venta d ON v.id_venta = d.id_venta
            GROUP BY v.id_venta, v.fecha, v.id_cliente, ev.nombre,
                     tv.nombre, v.id_usuario_vendedor
            ORDER BY v.fecha DESC
        """)
        ventas = cursor.fetchall()
        conn.close()

        # Consulta 2 (Auth): diccionarios de clientes y usuarios
        conn_a   = get_connection_auth()
        cursor_a = conn_a.cursor(dictionary=True)
        cursor_a.execute("SELECT id_cliente, nombre, apellido, telefono FROM cliente")
        clientes_map = {c['id_cliente']: c for c in cursor_a.fetchall()}
        cursor_a.execute("SELECT id_usuario, nombres FROM usuario")
        usuarios_map = {u['id_usuario']: u for u in cursor_a.fetchall()}
        conn_a.close()

        # Unión en memoria (equivalente al antiguo JOIN cross-BD)
        pedidos = []
        for v in ventas:
            cli = clientes_map.get(v['id_cliente']) or {}
            usr = usuarios_map.get(v['id_usuario_vendedor']) or {}
            pedidos.append({
                'N° Pedido':    v['id_venta'],
                'Fecha (Perú)': v['fecha'],
                'Cliente':      (f"{cli.get('nombre') or ''} {cli.get('apellido') or ''}").strip(),
                'Teléfono':     cli.get('telefono'),
                'Estado':       v['estado'],
                'Tipo':         v['tipo_venta'],
                'Responsable':  usr.get('nombres'),
                'Total (S/)':   v['total'],
            })

        # Intentar usar openpyxl si está disponible, si no usar CSV
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Pedidos"

            # Encabezado con estilo
            headers = list(pedidos[0].keys()) if pedidos else ['N° Pedido','Fecha (Perú)','Cliente','Teléfono','Estado','Tipo','Responsable','Total (S/)']
            header_fill = PatternFill("solid", fgColor="1A1A2E")
            header_font = Font(color="FFFFFF", bold=True)
            for col, h in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=h)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal='center')

            # Datos
            for row_idx, ped in enumerate(pedidos, 2):
                for col_idx, val in enumerate(ped.values(), 1):
                    cell = ws.cell(row=row_idx, column=col_idx, value=str(val) if val is not None else '')
                    if row_idx % 2 == 0:
                        cell.fill = PatternFill("solid", fgColor="EEF4FB")

            # Ancho de columnas automático
            for col in ws.columns:
                max_len = max((len(str(c.value or '')) for c in col), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

            output = io.BytesIO()
            wb.save(output)
            output.seek(0)
            response = make_response(output.read())
            response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            response.headers['Content-Disposition'] = 'attachment; filename=pedidos_cleoferr.xlsx'
            return response

        except ImportError:
            # Fallback a CSV si openpyxl no está instalado
            output = io.StringIO()
            if pedidos:
                writer = csv.DictWriter(output, fieldnames=pedidos[0].keys())
                writer.writeheader()
                for ped in pedidos:
                    writer.writerow({k: str(v) if v is not None else '' for k, v in ped.items()})
            response = make_response(output.getvalue())
            response.headers['Content-Type'] = 'text/csv; charset=utf-8'
            response.headers['Content-Disposition'] = 'attachment; filename=pedidos_cleoferr.csv'
            return response

    except Exception as e:
        flash(f'Error al exportar pedidos: {e}', 'danger')
        return redirect(url_for('pedidos'))


def _excel_response(rows, filename, sheet_name='Datos'):
    """Helper: genera respuesta Excel/CSV a partir de una lista de dicts."""
    import io, csv
    from flask import make_response
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_name
        if not rows:
            output = io.BytesIO()
            wb.save(output); output.seek(0)
            resp = make_response(output.read())
            resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
            return resp
        headers = list(rows[0].keys())
        hf = PatternFill("solid", fgColor="1A1A2E")
        hfont = Font(color="FFFFFF", bold=True)
        for col, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=col, value=h)
            c.fill = hf; c.font = hfont; c.alignment = Alignment(horizontal='center')
        alt = PatternFill("solid", fgColor="EEF4FB")
        for ri, row in enumerate(rows, 2):
            for ci, val in enumerate(row.values(), 1):
                cell = ws.cell(row=ri, column=ci, value=str(val) if val is not None else '')
                if ri % 2 == 0: cell.fill = alt
        for col in ws.columns:
            w = max((len(str(c.value or '')) for c in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(w + 4, 40)
        output = io.BytesIO()
        wb.save(output); output.seek(0)
        resp = make_response(output.read())
        resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return resp
    except ImportError:
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            for r in rows: writer.writerow({k: str(v) if v is not None else '' for k, v in r.items()})
        resp = make_response('\ufeff' + output.getvalue())
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename={filename.replace(".xlsx",".csv")}'
        return resp


@app.route('/reportes/productos/exportar')
@login_required
@escritura_required
def exportar_productos_excel():
    conn = get_connection_tienda(); cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT p.id_producto AS "ID", p.nombre AS "Producto",
                   c.nombre AS "Categoría", m.nombre AS "Marca",
                   p.precio AS "Precio (S/)", p.stock AS "Stock",
                   CASE WHEN p.activo THEN 'activo' ELSE 'inactivo' END AS "Estado"
            FROM producto p
            LEFT JOIN categoria c ON p.id_categoria = c.id_categoria
            LEFT JOIN marca m ON p.id_marca = m.id_marca
            ORDER BY p.id_producto
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()
    return _excel_response(rows, 'productos_cleoferr.xlsx', 'Productos')


@app.route('/reportes/clientes/exportar')
@login_required
@escritura_required
def exportar_clientes_excel():
    conn = get_connection_auth(); cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_cliente AS "ID",
                   CONCAT(COALESCE(nombre,''), ' ', COALESCE(apellido,'')) AS "Nombre completo",
                   email AS "Correo", telefono AS "Teléfono",
                   CASE WHEN activo THEN 'activo' ELSE 'inactivo' END AS "Estado"
            FROM cliente ORDER BY id_cliente
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()
    return _excel_response(rows, 'clientes_cleoferr.xlsx', 'Clientes')


@app.route('/reportes/proveedores/exportar')
@login_required
@escritura_required
def exportar_proveedores_excel():
    conn = get_connection_tienda(); cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_proveedor AS "ID", nombre AS "Proveedor",
                   contacto AS "Contacto", telefono AS "Teléfono",
                   email AS "Correo",
                   CASE WHEN activo THEN 'activo' ELSE 'inactivo' END AS "Estado"
            FROM proveedor ORDER BY id_proveedor
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()
    return _excel_response(rows, 'proveedores_cleoferr.xlsx', 'Proveedores')


@app.route('/reportes/inventario/exportar')
@login_required
@escritura_required
def exportar_inventario_excel():
    conn = get_connection_tienda(); cursor = conn.cursor(dictionary=True)
    try:
        # Consulta 1 (Tienda): movimientos + producto
        cursor.execute(f"""
            SELECT im.id_movimiento AS id,
                   {SQL_PARED_LIMA.format(p='im.', col='fecha')} AS fecha,
                   p.nombre AS producto,
                   im.tipo,
                   im.cantidad,
                   im.id_usuario,
                    COALESCE(im.observacion, '') AS motivo
            FROM inventario_movimiento im
            LEFT JOIN producto p ON im.id_producto = p.id_producto
            ORDER BY im.fecha DESC
        """)
        movimientos = cursor.fetchall()
        # Consulta 2 (Auth): nombres de usuarios
        conn_a = get_connection_auth()
        cursor_a = conn_a.cursor(dictionary=True)
        cursor_a.execute("SELECT id_usuario, nombres FROM usuario")
        usuarios_map = {u['id_usuario']: u['nombres'] for u in cursor_a.fetchall()}
        conn_a.close()
        # Unión en memoria
        rows = [{
            'ID': m['id'],
            'Fecha (Perú)': m['fecha'],
            'Producto': m['producto'],
            'Tipo': m['tipo'],
            'Cantidad': m['cantidad'],
            'Responsable': usuarios_map.get(m['id_usuario'], ''),
            'Motivo': m['motivo'],
        } for m in movimientos]
    except Exception:
        try:
            cursor.execute("""
                SELECT p.nombre AS "Producto", p.stock AS "Stock actual"
                 FROM producto p WHERE p.activo = TRUE ORDER BY p.nombre
            """)
            rows = cursor.fetchall()
        except Exception:
            rows = []
    finally:
        conn.close()
    return _excel_response(rows, 'inventario_cleoferr.xlsx', 'Inventario')


@app.route('/reportes/dia/exportar')
@login_required
@escritura_required
def exportar_ventas_dia_excel():
    conn = get_connection_tienda(); cursor = conn.cursor(dictionary=True)
    try:
        # Consulta 1 (Tienda): ventas del día (hora Perú) sin join a cliente
        cursor.execute(f"""
            SELECT v.id_venta,
                   {SQL_PARED_LIMA.format(p='v.', col='fecha')} AS fecha,
                   v.id_cliente,
                   ev.nombre AS estado,
                   tv.nombre AS tipo_venta,
                   COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
            FROM venta v
            LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
            LEFT JOIN tipo_venta tv ON v.id_tipo_venta = tv.id_tipo_venta
            LEFT JOIN detalle_venta d ON v.id_venta = d.id_venta
            WHERE {SQL_DIA_LIMA.format(p='v.', col='fecha')} = {SQL_HOY_LIMA}
            GROUP BY v.id_venta, v.fecha, v.id_cliente, ev.nombre, tv.nombre
            ORDER BY v.fecha DESC
        """)
        ventas = cursor.fetchall()
        # Consulta 2 (Auth): clientes + unión en memoria
        conn_a = get_connection_auth()
        cursor_a = conn_a.cursor(dictionary=True)
        cursor_a.execute("SELECT id_cliente, nombre, apellido FROM cliente")
        clientes_map = {c['id_cliente']: c for c in cursor_a.fetchall()}
        conn_a.close()
        rows = []
        for v in ventas:
            cli = clientes_map.get(v['id_cliente']) or {}
            rows.append({
                'N° Pedido': v['id_venta'],
                'Fecha/Hora (Perú)': v['fecha'],
                'Cliente': (f"{cli.get('nombre') or ''} {cli.get('apellido') or ''}").strip(),
                'Estado': v['estado'],
                'Tipo': v['tipo_venta'],
                'Total (S/)': v['total'],
            })
    finally:
        conn.close()
    return _excel_response(rows, 'ventas_hoy_cleoferr.xlsx', 'Ventas de hoy')


@app.route('/reportes/exportar')
@login_required
@escritura_required
def exportar_reportes_excel():
    return exportar_ventas_dia_excel()



@app.route('/productos/nuevo')
@login_required
@escritura_required
def nuevo_producto():
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM categoria")
    categorias = cursor.fetchall()
    cursor.execute("SELECT * FROM marca")
    marcas = cursor.fetchall()
    conn.close()
    return render_template('producto_form.html', categorias=categorias, marcas=marcas)


def _id_foranea(cursor, campo_form, tabla, id_col, etiqueta):
    """FK OPCIONAL del form: vacío o '0' → None; si viene, debe ser un
    dígito y existir en la tabla (parámetros preparados, sin 500)."""
    bruto = (request.form.get(campo_form) or '').strip()
    if not bruto or bruto == '0':
        return None, None
    if not bruto.isdigit():
        return None, f'La {etiqueta} seleccionada no es válida.'
    cursor.execute(f"SELECT 1 FROM {tabla} WHERE {id_col} = %s", (int(bruto),))
    if not cursor.fetchone():
        return None, f'La {etiqueta} seleccionada no existe.'
    return int(bruto), None


def _validar_producto(cursor):
    """Valida el form de alta/edición de producto (FASE 2).

    Recibe el cursor de la MISMA conexión que luego escribe. Devuelve
    (datos, None) o (None, mensaje). El NOMBRE de producto admite dígitos
    ('Taladro 650W'); precio y stock aceptan 0 (los CHECKs de la BD
    permiten >= 0) y rechazan negativos y no numéricos.
    """
    nombre, error = requerido('nombre', request.form.get('nombre'), 150)
    if error:
        return None, error
    precio, error = numero('precio', request.form.get('precio'))
    if error:
        return None, error
    if precio <= 0:
        return None, 'El campo «precio» debe ser mayor que cero.'
    stock, error = entero('stock', request.form.get('stock'))
    if error:
        return None, error
    descripcion = texto(request.form.get('descripcion'))
    if descripcion and len(descripcion) > 2000:
        return None, 'La descripción no puede superar 2000 caracteres.'
    estado = (request.form.get('estado') or '').strip()
    if estado not in ('activo', 'inactivo'):
        estado = 'activo'

    id_categoria, error = _id_foranea(cursor, 'id_categoria', 'categoria',
                                      'id_categoria', 'categoría')
    if error:
        return None, error
    id_marca, error = _id_foranea(cursor, 'id_marca', 'marca',
                                  'id_marca', 'marca')
    if error:
        return None, error
    return {'nombre': nombre, 'descripcion': descripcion, 'precio': precio,
            'stock': stock, 'id_categoria': id_categoria, 'id_marca': id_marca,
            'activo': estado == 'activo'}, None


@app.route('/productos/guardar', methods=['POST'])
@login_required
@escritura_required
def guardar_producto():
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        # FASE 2: validación de servidor con el módulo común — una entrada
        # malformada da un flash claro y vuelve al form, nunca un 500.
        datos, error = _validar_producto(cursor)
        if error:
            flash(error, 'danger')
            return redirect(url_for('nuevo_producto'))
        nombre_imagen = guardar_imagen('imagen')
        cursor.execute("""
            INSERT INTO producto (nombre, descripcion, precio, stock,
                                  id_categoria, id_marca, activo, imagen)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (datos['nombre'], datos['descripcion'], datos['precio'],
              datos['stock'], datos['id_categoria'], datos['id_marca'],
              datos['activo'], nombre_imagen))
        conn.commit()
        flash("Producto creado correctamente.", "success")
    except Exception:
        conn.rollback()
        app.logger.exception("Error al guardar el producto")
        flash('No se pudo guardar el producto. Revisa los datos e inténtalo de nuevo.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('productos'))


@app.route('/productos/editar/<int:id>')
@login_required
@escritura_required
def editar_producto(id):
    producto = db.get_or_404(Producto, id)
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM categoria")
    categorias = cursor.fetchall()
    cursor.execute("SELECT * FROM marca")
    marcas = cursor.fetchall()
    conn.close()
    return render_template('producto_form.html', producto=producto, categorias=categorias, marcas=marcas)


@app.route('/productos/actualizar/<int:id>', methods=['POST'])
@login_required
@escritura_required
def actualizar_producto(id):
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT imagen FROM producto WHERE id_producto = %s", (id,))
    fila = cursor.fetchone()
    if not fila:
        conn.close()
        abort(404)
    try:
        # FASE 2: misma validación que el alta; error → flash y vuelta al form
        datos, error = _validar_producto(cursor)
        if error:
            flash(error, 'danger')
            return redirect(url_for('editar_producto', id=id))
        nombre_imagen = guardar_imagen('imagen')
        if nombre_imagen and fila['imagen']:      # borra la imagen vieja
            ruta_vieja = os.path.join(UPLOAD_FOLDER, fila['imagen'])
            if os.path.exists(ruta_vieja):
                os.remove(ruta_vieja)
        cursor.execute("""
            UPDATE producto
               SET nombre = %s, descripcion = %s, precio = %s, stock = %s,
                   id_categoria = %s, id_marca = %s, activo = %s
             WHERE id_producto = %s
        """, (datos['nombre'], datos['descripcion'], datos['precio'],
              datos['stock'], datos['id_categoria'], datos['id_marca'],
              datos['activo'], id))
        if nombre_imagen:
            cursor.execute("UPDATE producto SET imagen = %s WHERE id_producto = %s",
                           (nombre_imagen, id))
        conn.commit()
        flash("Producto actualizado correctamente.", "success")
    except Exception:
        conn.rollback()
        app.logger.exception("Error al actualizar el producto %s", id)
        flash('No se pudo actualizar el producto. Revisa los datos e inténtalo de nuevo.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('productos'))


# C3d: borrado destructivo SOLO por POST con CSRF (antes GET sin protección).
@app.route('/productos/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_producto(id):
    # Regla del alcance (§6.3): no eliminar físicamente un producto con
    # historial (ventas/pedidos o movimientos de inventario); priorizar la
    # desactivación, que se hace desde Editar (estado -> inactivo).
    producto = db.get_or_404(Producto, id)
    ventas = db.session.execute(
        text("SELECT COUNT(*) FROM detalle_venta WHERE id_producto = :id"),
        {"id": id}).scalar()
    movimientos = db.session.execute(
        text("SELECT COUNT(*) FROM inventario_movimiento WHERE id_producto = :id"),
        {"id": id}).scalar()
    if ventas or movimientos:
        cantidad = ventas if ventas else movimientos
        motivo = ("ventas o pedidos" if ventas else "movimientos de inventario")
        flash(f"No se puede eliminar: el producto tiene {cantidad} "
              f"{motivo} asociados. Desactívalo desde Editar para retirarlo "
              "del catálogo sin perder el historial.", "danger")
        return redirect(url_for('productos'))
    db.session.delete(producto)
    db.session.commit()
    flash("Producto eliminado correctamente.", "success")
    return redirect(url_for('productos'))



@app.route('/catalogo')
def catalogo_cliente():
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    # Precio vigente por producto (misma fuente que el checkout): el
    # descuento de la oferta ACTIVA en curso lo calcula el servidor; la
    # plantilla solo lo renderiza (tachado + precio final + badge).
    cursor.execute(f"""
        SELECT p.*, c.nombre AS categoria, m.nombre AS marca,
               COALESCE(of.descuento_unitario, 0) AS descuento,
               p.precio - COALESCE(of.descuento_unitario, 0) AS precio_final,
               of.tipo AS oferta_tipo, of.valor AS oferta_valor
          FROM producto p
          LEFT JOIN categoria c ON p.id_categoria = c.id_categoria
          LEFT JOIN marca m ON p.id_marca = m.id_marca
          {SQL_PRECIO_VIGENTE.format(p="p.", a="of")}
         WHERE p.activo = TRUE
         ORDER BY p.id_producto
    """)
    lista = cursor.fetchall()
    conn.close()
    return render_template('catalogo.html', productos=lista)


# ── Gestión de Clientes ───────────────────────────────────────
@app.route('/clientes')
@login_required
@escritura_required
def clientes():
    conn   = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'cliente'
        ORDER BY ordinal_position
    """)
    columnas = [c['column_name'] for c in cursor.fetchall()]
    # P2: COUNT del conjunto completo + página (ORDER BY id DESC estable)
    cursor.execute("SELECT COUNT(*) AS total FROM cliente")
    total = cursor.fetchone()['total']
    pagina = _pagina_actual(total, POR_PAGINA_ADMIN)
    cursor.execute("SELECT * FROM cliente ORDER BY id_cliente DESC LIMIT %s OFFSET %s",
                   (POR_PAGINA_ADMIN, (pagina - 1) * POR_PAGINA_ADMIN))
    lista = cursor.fetchall()
    conn.close()
    return render_template('clientes.html', clientes=lista, columnas=columnas,
                           pagina=pagina, total=total, por_pagina=POR_PAGINA_ADMIN,
                           filtros_qs=_filtros_sin_pagina())


@app.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@escritura_required
def nuevo_cliente():
    if request.method == 'POST':
        nombre    = request.form['nombre']
        email     = _normalizar_correo(request.form['email'])
        telefono  = request.form.get('telefono', '')
        direccion = request.form.get('direccion', '')
        contrasena = request.form.get('contrasena', '123456')
        hash_pw   = bcrypt.generate_password_hash(contrasena).decode('utf-8')

        conn   = get_connection_auth()
        cursor = conn.cursor()
        try:
            # El formulario envía nombre y apellidos CONCATENADOS en "nombre"
            # (hidden nombreCompleto del cliente_form): se separan con el
            # MISMO criterio del POS (registrar_pos): primera palabra =
            # nombre, resto = apellido (el JS del form valida ambos).
            partes = nombre.split()
            nombre_db = partes[0] if partes else nombre
            apellido_db = ' '.join(partes[1:])
            # Sin documento: id_tipo_documento=1 (DNI) y nro_documento NULL,
            # criterio del registro web (registro_cliente). OJO: nunca ''
            # — la BD tiene UNIQUE (id_tipo_documento, nro_documento).
            cursor.execute(
                # activo se fija de forma explicita: si se deja al DEFAULT de la
                # columna (TRUE) el cliente queda "verificado" sin haberlo hecho y
                # despues no puede registrarse por la web ("ya esta registrado").
                "INSERT INTO cliente (nombre, apellido, email, telefono, direccion,"
                " contrasena, activo, id_tipo_documento, nro_documento) "
                "VALUES (%s,%s,%s,%s,%s,%s,TRUE,1,NULL)",
                (nombre_db, apellido_db, email, telefono, direccion, hash_pw)
            )
            conn.commit()
            flash("Cliente registrado correctamente.", "success")
        except Exception as e:
            conn.rollback()
            # El flash nunca oculta el bug: la excepción queda en el log.
            app.logger.exception("Error al registrar cliente desde el panel admin")
            flash(f"Error al registrar cliente: {e}", "danger")
        finally:
            conn.close()
        return redirect(url_for('clientes'))
    return render_template('cliente_form.html')


@app.route('/clientes/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@escritura_required
def editar_cliente(id):
    conn   = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    if request.method == 'POST':
        nombre    = request.form['nombre']
        email     = _normalizar_correo(request.form['email'])
        telefono  = request.form.get('telefono', '')
        direccion = request.form.get('direccion', '')
        # Documento de identidad (admin puede corregirlo)
        tipo_doc_nombre = (request.form.get('tipo_documento') or '').strip().lower()
        nro_documento  = normalizar_numero(request.form.get('nro_documento', ''))
        if tipo_doc_nombre and nro_documento:
            ok_doc, msg_doc = validar_documento(tipo_doc_nombre, nro_documento)
            if not ok_doc:
                flash(msg_doc, 'danger')
                return redirect(url_for('editar_cliente', id=id))
        else:
            tipo_doc_nombre = None
            nro_documento = None
        try:
            if tipo_doc_nombre:
                cursor.execute(
                    "SELECT id_tipo_documento FROM tipo_documento WHERE LOWER(TRIM(nombre)) = %s",
                    (tipo_doc_nombre,)
                )
                fila_td = cursor.fetchone()
                id_tipo_doc = fila_td['id_tipo_documento'] if fila_td else 1
                # Leer el documento ANTERIOR para auditoria
                cursor.execute(
                    "SELECT LOWER(TRIM(td.nombre)) AS tipo, c.nro_documento"
                    "  FROM cliente c"
                    "  LEFT JOIN tipo_documento td ON td.id_tipo_documento = c.id_tipo_documento"
                    " WHERE c.id_cliente = %s",
                    (id,)
                )
                doc_anterior = cursor.fetchone() or {}
                doc_anterior_txt = f"{doc_anterior.get('tipo') or ''}:{doc_anterior.get('nro_documento') or ''}"
                cursor.execute(
                    "UPDATE cliente SET nombre=%s, email=%s, telefono=%s, direccion=%s,"
                    " id_tipo_documento=%s, nro_documento=%s WHERE id_cliente=%s",
                    (nombre, email, telefono, direccion, id_tipo_doc, nro_documento or None, id)
                )
                # AUDITORIA (TIENDA): quien cambio el documento de quien
                if doc_anterior_txt != f"{tipo_doc_nombre}:{nro_documento}":
                    conn_t = get_connection_tienda()
                    cur_t = conn_t.cursor()
                    try:
                        cur_t.execute(
                            "INSERT INTO auditoria (id_usuario, tabla_afectada, id_registro,"
                            " accion, valor_anterior, valor_nuevo)"
                            " VALUES (%s, 'cliente_documento', %s, 'admin_cambio_documento', %s, %s)",
                            (session.get('usuario_id'), id, doc_anterior_txt,
                             f"{tipo_doc_nombre}:{nro_documento}")
                        )
                        conn_t.commit()
                    except Exception:
                        conn_t.rollback()
                        app.logger.exception('Error auditando cambio de documento')
                    finally:
                        conn_t.close()
            else:
                cursor.execute(
                    "UPDATE cliente SET nombre=%s, email=%s, telefono=%s, direccion=%s WHERE id_cliente=%s",
                    (nombre, email, telefono, direccion, id)
                )
            conn.commit()
            flash("Cliente actualizado correctamente.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error: {e}", "danger")
        finally:
            conn.close()
        return redirect(url_for('clientes'))

    cursor.execute("SELECT * FROM cliente WHERE id_cliente = %s", (id,))
    cliente = cursor.fetchone()
    cursor.execute("SELECT LOWER(TRIM(nombre)) AS nombre FROM tipo_documento ORDER BY id_tipo_documento")
    tipos_doc = [r['nombre'] for r in cursor.fetchall()]
    cursor.execute(
        "SELECT LOWER(TRIM(td.nombre)) AS tipo FROM cliente c"
        " LEFT JOIN tipo_documento td ON td.id_tipo_documento = c.id_tipo_documento"
        " WHERE c.id_cliente = %s", (id,))
    fila_doc = cursor.fetchone()
    doc_tipo_actual = fila_doc['tipo'] if fila_doc and fila_doc['tipo'] else ''
    conn.close()
    if not cliente:
        flash("Cliente no encontrado.", "danger")
        return redirect(url_for('clientes'))
    return render_template('cliente_form.html', cliente=cliente,
                           tipos_doc=tipos_doc, doc_tipo_actual=doc_tipo_actual)


# C3d: borrado destructivo SOLO por POST con CSRF (antes GET sin protección).
@app.route('/clientes/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_cliente(id):
    conn   = get_connection_auth()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM cliente WHERE id_cliente = %s", (id,))
        conn.commit()
        flash("Cliente eliminado.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for('clientes'))


# ── Registro rápido de cliente desde el Punto de Venta (por DNI/RUC) ──────
# Boleta  → DNI (8 dígitos) + nombre (autocompletado vía RENIEC en el frontend).
# Factura → RUC (11 dígitos, inicia en 10 o 20) + razón social + dirección fiscal.
# id_tipo_documento: 1 = DNI, 6 = RUC (catálogo SUNAT). Los clientes POS se
# crean con origen='pos' y sin email ni contraseña (no pueden iniciar sesión
# web, pero sí aparecen en ventas/reportes).
# (Integrado desde origin/Lesly; consulta RUC/DNI va por /api/consultar_* del
#  frontend, nunca con token expuesto.)
@app.route('/clientes/registrar_pos', methods=['POST'])
@login_required
@escritura_required
def registrar_pos():
    data = request.get_json(silent=True) or {}
    tipo = (data.get('tipo') or 'boleta').strip().lower()
    if tipo not in ('boleta', 'factura'):
        return {'ok': False, 'error': 'Tipo de comprobante inválido.'}, 400

    nombre    = (data.get('nombre') or '').strip()
    telefono  = (data.get('telefono') or '').strip()
    apellido  = (data.get('apellido') or '').strip()
    direccion = (data.get('direccion') or '').strip()

    if tipo == 'boleta':
        doc = (data.get('dni') or '').strip()
        if not doc.isdigit() or len(doc) != 8:
            return {'ok': False, 'error': 'El DNI debe tener exactamente 8 dígitos.'}, 400
        if len(nombre) < 3:
            return {'ok': False, 'error': 'El nombre del cliente es obligatorio (mínimo 3 caracteres).'}, 400
    else:
        doc = (data.get('ruc') or '').strip()
        if not doc.isdigit() or len(doc) != 11 or doc[:2] not in ('10', '20'):
            return {'ok': False, 'error': 'El RUC debe tener 11 dígitos y comenzar con 10 o 20.'}, 400
        if len(nombre) < 3:
            return {'ok': False, 'error': 'La razón social es obligatoria (mínimo 3 caracteres).'}, 400
        if len(direccion) < 5:
            return {'ok': False, 'error': 'La dirección fiscal es obligatoria para factura (mínimo 5 caracteres).'}, 400

    if telefono and (not telefono.isdigit() or len(telefono) != 9):
        return {'ok': False, 'error': 'El teléfono debe tener exactamente 9 dígitos.'}, 400

    id_tipo_doc = 1 if tipo == 'boleta' else 6
    conn   = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    try:
        # ¿Ya existe un cliente con ese documento? → se reutiliza (no se duplica)
        cursor.execute("""
            SELECT id_cliente, nombre, apellido, telefono FROM cliente
            WHERE id_tipo_documento = %s AND nro_documento = %s
        """, (id_tipo_doc, doc))
        existente = cursor.fetchone()
        # Buscar el id del tipo de documento POR NOMBRE (no por id).
        cursor.execute("""
            SELECT id_tipo_documento FROM tipo_documento
             WHERE LOWER(TRIM(nombre)) = %s
        """, (tipo_doc_nombre,))
        fila_td = cursor.fetchone()
        _id_tipo_doc = fila_td['id_tipo_documento'] if fila_td else 1

        if existente:
            if telefono and not (existente.get('telefono') or '').strip():
                cursor.execute(
                    "UPDATE cliente SET telefono = %s WHERE id_cliente = %s",
                    (telefono, existente['id_cliente']))
                conn.commit()
            nombre_existente = f"{existente.get('nombre') or ''} {existente.get('apellido') or ''}".strip()
            return {'ok': True, 'id_cliente': existente['id_cliente'],
                    'nombre': nombre_existente, 'ya_existia': True}

        # `apellido` es NOT NULL en la BD. Si no se envió y es boleta (RENIEC:
        # Nombres ApellidoPaterno ApellidoMaterno), separamos el nombre completo;
        # como último recurso usamos '' (respeta NOT NULL). En factura, la razón
        # social se guarda completa en `nombre` y `apellido` queda ''.
        nombre_db   = nombre
        apellido_db = apellido
        if not apellido_db and tipo == 'boleta':
            partes = nombre.split()
            if len(partes) >= 2:
                nombre_db   = partes[0]
                apellido_db = ' '.join(partes[1:])
        cursor.execute("""
            INSERT INTO cliente (nombre, apellido, email, telefono, id_tipo_documento,
                                 nro_documento, direccion, contrasena, activo, origen)
            VALUES (%s, %s, NULL, %s, %s, %s, %s, NULL, TRUE, 'pos')
            RETURNING id_cliente
        """, (nombre_db, apellido_db, telefono or None, id_tipo_doc, doc,
              direccion or None))
        id_cliente = cursor.fetchone()['id_cliente']
        conn.commit()
        nombre_mostrar = f"{nombre_db} {apellido_db}".strip()
        return {'ok': True, 'id_cliente': id_cliente, 'nombre': nombre_mostrar,
                'ya_existia': False}
    except Exception:
        conn.rollback()
        app.logger.exception("Error al registrar cliente desde el POS")
        return {'ok': False, 'error': 'No se pudo guardar el cliente. Intenta de nuevo.'}, 500
    finally:
        conn.close()


# ── Inventario ────────────────────────────────────────────────
@app.route('/inventario')
@login_required
@escritura_required
def inventario():
    # Consulta 1 (Tienda): movimientos + producto + proveedor
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    # P2: COUNT del conjunto completo + página (ORDER BY estable con id
    # de desempate; el historial ya no se recorta a 200 fijos)
    cursor.execute("SELECT COUNT(*) AS total FROM inventario_movimiento")
    total = cursor.fetchone()['total']
    pagina = _pagina_actual(total, POR_PAGINA_ADMIN)
    cursor.execute("""
        SELECT i.*, p.nombre AS producto_nombre, pr.nombre AS proveedor_nombre
        FROM inventario_movimiento i
        LEFT JOIN producto p ON i.id_producto = p.id_producto
        LEFT JOIN proveedor pr ON i.id_proveedor = pr.id_proveedor
        ORDER BY i.fecha DESC, i.id_movimiento DESC
        LIMIT %s OFFSET %s
    """, (POR_PAGINA_ADMIN, (pagina - 1) * POR_PAGINA_ADMIN))
    movimientos = cursor.fetchall()

    cursor.execute("SELECT * FROM producto WHERE activo = TRUE ORDER BY nombre")
    productos = cursor.fetchall()
    cursor.execute("SELECT * FROM proveedor ORDER BY nombre")
    proveedores = cursor.fetchall()
    conn.close()

    # Consulta 2 (Auth): usuarios y vendedores
    conn_a   = get_connection_auth()
    cursor_a = conn_a.cursor(dictionary=True)
    cursor_a.execute("SELECT id_usuario, nombres FROM usuario")
    usuarios_map = {u['id_usuario']: u['nombres'] for u in cursor_a.fetchall()}
    cursor_a.execute("""
        SELECT u.id_usuario, u.nombres
        FROM usuario u
        INNER JOIN rol r ON u.id_rol = r.id_rol
        WHERE r.nombre = 'vendedor'
        ORDER BY u.nombres
    """)
    vendedores = cursor_a.fetchall()
    conn_a.close()

    # Unión en memoria: añadir nombre de usuario a cada movimiento
    for m in movimientos:
        m['usuario_nombre'] = usuarios_map.get(m.get('id_usuario'))
    return render_template('inventario.html',
                           movimientos=movimientos,
                           productos=productos,
                           proveedores=proveedores,
                           vendedores=vendedores,
                           pagina=pagina, total=total,
                           por_pagina=POR_PAGINA_ADMIN,
                           filtros_qs=_filtros_sin_pagina())


@app.route('/inventario/registrar', methods=['POST'])
@login_required
@escritura_required
def registrar_movimiento():
    # FASE 2: validación de servidor con el módulo común — nada de 500
    # por entradas malformadas ni flashes con texto crudo de la BD.
    tipo = (request.form.get('tipo') or '').strip()
    if tipo not in ('entrada', 'salida'):
        flash('El tipo de movimiento debe ser entrada o salida.', 'danger')
        return redirect(url_for('inventario'))
    id_producto, error = entero('producto', request.form.get('id_producto'))
    if error:
        flash(error, 'danger')
        return redirect(url_for('inventario'))
    cantidad, error = entero('cantidad', request.form.get('cantidad'))
    if error:
        flash(error, 'danger')
        return redirect(url_for('inventario'))
    if cantidad <= 0:
        flash('El campo «cantidad» debe ser mayor que cero.', 'danger')
        return redirect(url_for('inventario'))
    # Precio unitario OPCIONAL: si viene, debe ser número >= 0
    precio_unit = texto(request.form.get('precio_unitario'))
    if precio_unit is not None:
        precio_unit, error = numero('precio unitario', precio_unit)
        if error:
            flash(error, 'danger')
            return redirect(url_for('inventario'))
    observacion = texto(request.form.get('observacion'))
    # Para compatibilidad: recibimos ambos campos, uno estará vacío según tipo
    id_proveedor = None
    id_vendedor = None
    if tipo == 'entrada' and texto(request.form.get('id_proveedor')):
        id_proveedor, error = entero('proveedor', request.form.get('id_proveedor'))
        if error:
            flash(error, 'danger')
            return redirect(url_for('inventario'))
    if tipo == 'salida' and texto(request.form.get('id_vendedor')):
        id_vendedor, error = entero('vendedor', request.form.get('id_vendedor'))
        if error:
            flash(error, 'danger')
            return redirect(url_for('inventario'))
    id_usuario = session['usuario_id']

    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT stock FROM producto WHERE id_producto=%s", (id_producto,))
        prod = cursor.fetchone()
        if not prod:
            flash("Producto no encontrado.", "danger")
            return redirect(url_for('inventario'))

        if id_proveedor:
            cursor.execute("SELECT 1 FROM proveedor WHERE id_proveedor=%s",
                           (id_proveedor,))
            if not cursor.fetchone():
                flash("El proveedor seleccionado no existe.", "danger")
                return redirect(url_for('inventario'))

        stock_actual = prod['stock']
        if tipo == 'salida' and cantidad > stock_actual:
            flash(f"Stock insuficiente. Disponible: {stock_actual}", "danger")
            return redirect(url_for('inventario'))

        nuevo_stock = stock_actual + cantidad if tipo == 'entrada' else stock_actual - cantidad

        # Si es salida y se seleccionó un vendedor, obtener su nombre y añadir a observación
        # (la tabla usuario vive en la BD Auth, por eso se consulta por separado)
        if tipo == 'salida' and id_vendedor:
            try:
                conn_a   = get_connection_auth()
                cursor_a = conn_a.cursor(dictionary=True)
                cursor_a.execute("SELECT nombres FROM usuario WHERE id_usuario=%s", (id_vendedor,))
                row = cursor_a.fetchone()
                conn_a.close()
                nombre_vendedor = row['nombres'] if row else None
                if nombre_vendedor:
                    observacion = f"Vendedor: {nombre_vendedor}" + (f" - {observacion}" if observacion else "")
            except Exception:
                # no crítico, continuar con la observación sin nombre
                app.logger.exception("Error consultando nombre del vendedor id=%s para observación", id_vendedor)

        # Para compatibilidad con la estructura actual, solo llenamos id_proveedor para entradas.
        proveedor_db_value = id_proveedor if tipo == 'entrada' and id_proveedor else None

        cursor.execute("""
            INSERT INTO inventario_movimiento
                (tipo, id_producto, id_proveedor, cantidad, precio_unitario, observacion, id_usuario, stock_resultante)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (tipo, id_producto, proveedor_db_value, cantidad, precio_unit, observacion, id_usuario, nuevo_stock))

        cursor.execute("UPDATE producto SET stock=%s WHERE id_producto=%s", (nuevo_stock, id_producto))
        conn.commit()
        flash(f"Movimiento de {'entrada' if tipo=='entrada' else 'salida'} registrado. Nuevo stock: {nuevo_stock}", "success")
    except Exception:
        conn.rollback()
        app.logger.exception("Error al registrar el movimiento de inventario")
        flash('No se pudo registrar el movimiento. Revisa los datos e inténtalo de nuevo.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('inventario'))


# ── Proveedores ───────────────────────────────────────────────
@app.route('/proveedores')
@login_required
@escritura_required
def proveedores():
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    # P2: COUNT del conjunto completo + página (ORDER BY nombre, id)
    cursor.execute("SELECT COUNT(*) AS total FROM proveedor")
    total = cursor.fetchone()['total']
    pagina = _pagina_actual(total, POR_PAGINA_ADMIN)
    cursor.execute("SELECT * FROM proveedor ORDER BY nombre, id_proveedor"
                   " LIMIT %s OFFSET %s",
                   (POR_PAGINA_ADMIN, (pagina - 1) * POR_PAGINA_ADMIN))
    lista = cursor.fetchall()
    conn.close()
    return render_template('proveedores.html', proveedores=lista,
                           pagina=pagina, total=total, por_pagina=POR_PAGINA_ADMIN,
                           filtros_qs=_filtros_sin_pagina())


@app.route('/proveedores/nuevo', methods=['GET', 'POST'])
@login_required
@escritura_required
def nuevo_proveedor():
    if request.method == 'POST':
        nombre    = request.form['nombre']
        contacto  = request.form.get('contacto', '')
        telefono  = request.form.get('telefono', '')
        email     = request.form.get('email', '')
        direccion = request.form.get('direccion', '')
        conn      = get_connection_tienda()
        cursor    = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO proveedor (nombre, contacto, telefono, email, direccion) VALUES (%s,%s,%s,%s,%s)",
                (nombre, contacto, telefono, email, direccion)
            )
            conn.commit()
            flash("Proveedor registrado.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error: {e}", "danger")
        finally:
            conn.close()
        return redirect(url_for('proveedores'))
    return render_template('proveedor_form.html')


@app.route('/proveedores/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@escritura_required
def editar_proveedor(id):
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    if request.method == 'POST':
        nombre    = request.form['nombre']
        contacto  = request.form.get('contacto', '')
        telefono  = request.form.get('telefono', '')
        email     = request.form.get('email', '')
        direccion = request.form.get('direccion', '')
        try:
            cursor.execute(
                "UPDATE proveedor SET nombre=%s, contacto=%s, telefono=%s, email=%s, direccion=%s WHERE id_proveedor=%s",
                (nombre, contacto, telefono, email, direccion, id)
            )
            conn.commit()
            flash("Proveedor actualizado.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error: {e}", "danger")
        finally:
            conn.close()
        return redirect(url_for('proveedores'))
    cursor.execute("SELECT * FROM proveedor WHERE id_proveedor=%s", (id,))
    proveedor = cursor.fetchone()
    conn.close()
    return render_template('proveedor_form.html', proveedor=proveedor)


@app.route('/proveedores/eliminar/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def eliminar_proveedor(id):
    conn   = get_connection_tienda()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM proveedor WHERE id_proveedor=%s", (id,))
        conn.commit()
        flash("Proveedor eliminado.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for('proveedores'))


# ── Paginación en servidor (P1..P4) ─────────────────────────────
POR_PAGINA_ADMIN = 20   # listados admin
POR_PAGINA_CLIENTE = 10 # páginas del cliente (mis_pedidos, mis_comprobantes)


def _pagina_actual(total, por_pagina):
    """?pagina= saneada: no numérica o <1 → 1; > última → última. Nunca 500."""
    try:
        p = int(request.args.get('pagina', 1))
    except (TypeError, ValueError):
        p = 1
    ultima = max(1, -(-total // por_pagina))
    return min(max(p, 1), ultima)


def _filtros_sin_pagina():
    """Query string de los filtros activos SIN 'pagina' (la macro de
    paginación lo pega a cada enlace). Se calcula aquí en Python porque
    en Jinja un {% set %} dentro de un {% for %} no sale del bucle."""
    return urlencode([(k, v) for k, v in request.args.items(multi=True)
                      if k != 'pagina'])


# ── Pedidos ─────────────────────────────────────────────────
@app.route('/pedidos')
@login_required
@escritura_required
def pedidos():
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    # Estados válidos leídos de la tabla estado_venta (nunca desincronizados del HTML)
    cursor.execute("""
        SELECT nombre FROM estado_venta WHERE activo = TRUE ORDER BY orden
    """)
    lista_estados = [r['nombre'] for r in cursor.fetchall()]
    PASOS = [e for e in lista_estados if e != 'cancelado']

    # RF9: filtros del listado aplicados EN SERVIDOR, validados y
    # parametrizados (entradas maliciosas no generan 500: se ignoran
    # con un aviso). Fechas en días calendario Lima (services/horario).
    filtros_sql, filtros_params = [], []
    for campo in ('desde', 'hasta'):
        valor = (request.args.get(campo) or '').strip()
        if not valor:
            continue
        try:
            fecha = datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            flash('Fecha inválida, filtro ignorado.', 'warning')
            continue
        operador = '>=' if campo == 'desde' else '<='
        filtros_sql.append(
            f"{SQL_DIA_LIMA.format(p='v.', col='fecha')} {operador} %s")
        filtros_params.append(fecha)
    estado_f = (request.args.get('estado') or '').strip()
    if estado_f:
        if estado_f not in lista_estados:
            flash('Estado no válido, filtro ignorado.', 'warning')
        else:
            filtros_sql.append("ev.nombre = %s")
            filtros_params.append(estado_f)
    q = (request.args.get('q') or '').strip()
    id_venta_q = int(q) if q.isdigit() else None

    total, pagina = 0, 1
    try:
        # Consulta (Auth) primero: clientes y usuarios. El filtro q de
        # cliente se resuelve aquí en memoria (la BD Auth es aparte) y
        # los ids coincidentes viajan parametrizados a la consulta de
        # tienda; si q es todo dígitos también casa el código de venta.
        conn_a   = get_connection_auth()
        cursor_a = conn_a.cursor(dictionary=True)
        cursor_a.execute("SELECT id_cliente, nombre, apellido, telefono FROM cliente")
        clientes_map = {c['id_cliente']: c for c in cursor_a.fetchall()}
        cursor_a.execute("SELECT id_usuario, nombres FROM usuario")
        usuarios_map = {u['id_usuario']: u for u in cursor_a.fetchall()}
        conn_a.close()

        if q:
            clientes_q = [cid for cid, c in clientes_map.items()
                          if q.lower() in (f"{c.get('nombre') or ''} "
                                           f"{c.get('apellido') or ''}").lower()]
            if clientes_q or id_venta_q is not None:
                if id_venta_q is not None:
                    filtros_sql.append("(v.id_cliente = ANY(%s) OR v.id_venta = %s)")
                    filtros_params += [clientes_q, id_venta_q]
                else:
                    filtros_sql.append("v.id_cliente = ANY(%s)")
                    filtros_params.append(clientes_q)
            else:
                filtros_sql.append("FALSE")   # q sin coincidencias: vacío

        where_sql = (" WHERE " + " AND ".join(filtros_sql)) if filtros_sql else ""
        # P1: paginación en servidor — COUNT del conjunto FILTRADO completo
        # y LIMIT/OFFSET parametrizados; ORDER BY estable con id de desempate
        # (ventas del mismo instante no intercambian página entre recargas).
        cursor.execute("SELECT COUNT(*) AS total FROM venta v"
                       " LEFT JOIN estado_venta ev"
                       " ON v.id_estado_venta = ev.id_estado_venta" + where_sql,
                       filtros_params)
        total = cursor.fetchone()['total']
        pagina = _pagina_actual(total, POR_PAGINA_ADMIN)
        # Consulta 1 (Tienda): ventas + total, sin joins cross-BD
        cursor.execute(f"""
            SELECT
                v.id_venta      AS id_pedido,
                {SQL_PARED_LIMA.format(p='v.', col='fecha')} AS fecha,
                tv.nombre    AS tipo_venta,
                ev.nombre    AS estado,
                v.id_cliente,
                v.id_usuario_vendedor,
                COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
            FROM venta v
            LEFT JOIN tipo_venta tv ON v.id_tipo_venta = tv.id_tipo_venta
            LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
            LEFT JOIN detalle_venta d ON d.id_venta = v.id_venta
            {where_sql}
            GROUP BY v.id_venta, v.fecha, tv.nombre, ev.nombre,
                     v.id_cliente, v.id_usuario_vendedor
            ORDER BY v.fecha DESC, v.id_venta DESC
            LIMIT %s OFFSET %s
        """, filtros_params + [POR_PAGINA_ADMIN, (pagina - 1) * POR_PAGINA_ADMIN])
        pedidos_raw = cursor.fetchall()

        # Unión en memoria (equivalente al JOIN cross-BD)
        for ped in pedidos_raw:
            cli = clientes_map.get(ped['id_cliente']) or {}
            usr = usuarios_map.get(ped['id_usuario_vendedor']) or {}
            ped['cliente_nombre']    = (f"{cli.get('nombre') or ''} {cli.get('apellido') or ''}").strip() or None
            ped['cliente_telefono']  = cli.get('telefono')
            ped['responsable']       = usr.get('nombres')

        pedidos = []
        for ped in pedidos_raw:
            estado = ped.get('estado') or 'pendiente'
            ped['idx_actual'] = PASOS.index(estado) if estado in PASOS else -1
            ped['progreso'] = int((ped['idx_actual'] + 1) * 20) if ped['idx_actual'] >= 0 else 0
            # Venta local ya cobrada: NO aplica el flujo de despacho/entrega.
            ped['es_local_entregado'] = (ped.get('tipo_venta') == 'local' and estado == 'entregado')
            nombre   = ped.get('cliente_nombre') or 'Cliente'
            telefono = (ped.get('cliente_telefono') or '').strip()
            # El aviso por WhatsApp ("pedido procesado y listo") es del flujo
            # ONLINE (delivery). Las ventas locales se entregan en mostrador.
            if telefono and ped.get('tipo_venta') != 'local':
                from urllib.parse import quote
                if telefono.startswith('+'):
                    telefono = telefono[1:]
                if not telefono.startswith('51'):
                    telefono = '51' + telefono
                msg = quote(
                    f"Hola {nombre}, le saludamos de la Ferretería Inversiones CLEOFERR. "
                    "Le informamos que su pedido ya ha sido procesado en nuestro sistema "
                    "y se encuentra listo. ¡Muchas gracias por su preferencia!"
                )
                ped['url_whatsapp_pedido'] = f"https://wa.me/{telefono}?text={msg}"
            else:
                ped['url_whatsapp_pedido'] = None
            pedidos.append(ped)
    except Exception as e:
        print(f"ERROR en /pedidos: {e}")
        pedidos = []
    finally:
        conn.close()
    return render_template('pedidos.html', pedidos=pedidos, estados=lista_estados,
                           transiciones=TRANSICIONES_PEDIDO,
                           pagina=pagina, total=total,
                           por_pagina=POR_PAGINA_ADMIN,
                           filtros_qs=_filtros_sin_pagina())

@app.route('/ventas/nueva', methods=['GET'])
@login_required
@escritura_required
def venta_nueva_form():
    clientes  = []
    productos = []
    try:
        # Consulta 1 (Auth): clientes activos
        conn_a   = get_connection_auth()
        cursor_a = conn_a.cursor(dictionary=True)
        cursor_a.execute("""
            SELECT c.id_cliente,
                   CONCAT(c.nombre, ' ', COALESCE(c.apellido,'')) AS nombre,
                   c.telefono
            FROM cliente c WHERE COALESCE(c.activo, TRUE) = TRUE ORDER BY c.nombre
        """)
        clientes = cursor_a.fetchall()
        conn_a.close()
        # Consulta 2 (Tienda): productos con stock
        conn   = get_connection_tienda()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id_producto, nombre, precio, stock
            FROM producto WHERE activo = TRUE AND stock > 0 ORDER BY nombre
        """)
        productos = cursor.fetchall()
        conn.close()
    except Exception as e:
        app.logger.exception("Error en /ventas/nueva GET: %s", e)
    # Token de un solo uso: cada formulario solo puede cobrar UNA vez.
    # Evita dobles cobros por doble clic / re-envío del mismo POST.
    if not session.get('venta_token'):
        session['venta_token'] = secrets.token_hex(16)
    return render_template('venta_form.html', clientes=clientes, productos=productos,
                           venta_token=session.get('venta_token'))


@app.route('/ventas/nueva', methods=['POST'])
@login_required
@escritura_required
def venta_nueva_guardar():
    # Protección anti doble cobro: el token se genera en cada GET y se consume
    # con la primera venta. Si se re-envía el mismo formulario (doble clic,
    # F5/back + submit) el token ya no coincide y se rechaza el duplicado.
    venta_token = request.form.get('venta_token')
    if session.get('venta_token') and venta_token != session['venta_token']:
        flash('Esta venta ya fue registrada: no se permite un doble cobro.', 'warning')
        return redirect(url_for('pedidos'))
    # Consumir el ticket: esta página de cobro no puede volver a procesarse.
    session['venta_token'] = secrets.token_hex(16)

    id_cliente   = request.form.get('id_cliente') or None
    tipo_venta   = request.form.get('tipo_venta', 'local')
    metodo_pago  = request.form.get('metodo_pago', 'efectivo')
    metodo_db    = 'yape_plin' if metodo_pago == 'yape' else (
                   'tarjeta'   if metodo_pago == 'tarjeta' else 'efectivo')
    ids_producto = request.form.getlist('id_producto[]')
    cantidades   = request.form.getlist('cantidad[]')

    if not ids_producto:
        flash('Debes agregar al menos un producto.', 'danger')
        return redirect(url_for('venta_nueva_form'))
    if not id_cliente:
        flash('Debes seleccionar un cliente para la venta.', 'danger')
        return redirect(url_for('venta_nueva_form'))

    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    caja_activa = None
    try:
        # Las ventas LOCALES (presenciales) requieren una caja abierta;
        # las ventas online no dependen de la caja.
        if tipo_venta == 'local':
            cursor.execute("SELECT * FROM caja WHERE estado = 'abierta'")
            caja_activa = cursor.fetchone()
            if not caja_activa:
                flash('No puedes registrar una venta local sin una caja abierta. Abre caja primero en /caja.', 'danger')
                return redirect(url_for('caja'))

        # Crear la venta. Las ventas LOCALES (mostrador) se entregan al momento:
        # se crean directamente en estado 'entregado'; solo los pedidos online
        # pasan por estados intermedios (pendiente, procesando, enviado).
        estado_inicial = 'entregado' if tipo_venta == 'local' else 'pendiente'
        cursor.execute("""
            INSERT INTO venta (id_tipo_venta, id_cliente, id_usuario_vendedor, id_estado_venta)
            VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre = %s), %s, %s,
                    (SELECT id_estado_venta FROM estado_venta WHERE nombre = %s))
            RETURNING id_venta
        """, (tipo_venta, id_cliente, session.get('usuario_id'), estado_inicial))
        id_venta = cursor.fetchone()['id_venta']

        # Insertar cada ítem, descontar stock y registrar el movimiento de salida
        for id_prod, cant in zip(ids_producto, cantidades):
            try:
                cant = int(cant) if cant else 1
            except (TypeError, ValueError):
                cant = 1
            if cant <= 0:
                raise ValueError("Las cantidades deben ser mayores a cero.")
            cursor.execute("SELECT precio, stock, nombre FROM producto WHERE id_producto = %s", (id_prod,))
            prod = cursor.fetchone()
            if not prod:
                continue
            # No permitir vender más stock del disponible (evita stock negativo)
            if cant > (prod['stock'] or 0):
                raise ValueError(
                    f"Stock insuficiente de \"{prod['nombre']}\" "
                    f"(disponible: {prod['stock']} unidades). Venta no registrada."
                )
            cursor.execute("""
                INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario)
                VALUES (%s, %s, %s, %s)
            """, (id_venta, id_prod, cant, prod['precio']))
            cursor.execute("""
                UPDATE producto SET stock = stock - %s WHERE id_producto = %s
                RETURNING stock
            """, (cant, id_prod))
            nuevo_stock = cursor.fetchone()['stock']
            cursor.execute("""
                INSERT INTO inventario_movimiento
                    (tipo, id_producto, id_proveedor, cantidad, precio_unitario,
                     observacion, id_usuario, stock_resultante)
                VALUES ('salida', %s, NULL, %s, %s, %s, %s, %s)
            """, (id_prod, cant, prod['precio'],
                  f'Venta {"local" if tipo_venta == "local" else "online"} #{id_venta}',
                  session.get('usuario_id'), nuevo_stock))

        # Registrar el pago y el ingreso en caja para ventas locales
        if tipo_venta == 'local' and caja_activa:
            cursor.execute("""
                INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado)
                VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre = %s), %s, 'confirmado')
            """, (id_venta, metodo_db, caja_activa['id_caja']))
            cursor.execute(f"""
                INSERT INTO movimiento_caja (id_caja, tipo, monto, concepto, id_usuario)
                SELECT %s, 'ingreso',
                       COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0), %s, %s
                FROM detalle_venta WHERE id_venta = %s
            """, (caja_activa['id_caja'], f'Venta local #{id_venta}',
                  session.get('usuario_id'), id_venta))

        # ── Comprobante de la venta local (boleta/factura) ────────────
        # En la misma transacción junto con pago, caja, stock y movimientos.
        # Tipo: lo decide el modal POS (comp_tipo); por defecto boleta.
        # Numeración: correlativa por serie vía helper (B000001 / F000001).
        if tipo_venta == 'local':
            comp_tipo = request.form.get('comp_tipo', 'boleta').strip().lower()
            tipo_comp_db = 'factura' if comp_tipo == 'factura' else 'boleta'
            doc_cliente = (request.form.get('comp_doc') or '').strip() or None
            if tipo_comp_db == 'factura' and (not doc_cliente or len(doc_cliente) != 11):
                flash('Factura requiere un RUC válido (11 dígitos). Se emitió boleta.', 'warning')
                tipo_comp_db = 'boleta'
                doc_cliente = None
            num_comp = _siguiente_numero_comprobante(cursor, tipo_comp_db)
            # Totales calculados a partir del detalle (precios con IGV 18% incluido)
            cursor.execute(f"""
                SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
                FROM detalle_venta WHERE id_venta = %s
            """, (id_venta,))
            _total        = round(float(cursor.fetchone()['total'] or 0), 2)
            _subtotal     = round(_total / 1.18, 2)
            _igv          = round(_total - _subtotal, 2)
            cursor.execute("""
                INSERT INTO comprobante
                    (id_venta, id_tipo_comprobante, numero, ruc, serie, subtotal, igv, total)
                VALUES (%s, (SELECT id_tipo_comprobante FROM tipo_comprobante WHERE nombre = %s),
                        %s, %s, %s, %s, %s, %s)
            """, (id_venta, tipo_comp_db, num_comp,
                  doc_cliente if tipo_comp_db == 'factura' else None,
                  'F001' if tipo_comp_db == 'factura' else 'B001',
                  _subtotal, _igv, _total))

        conn.commit()
        flash(f'Venta #{id_venta} registrada correctamente.', 'success')
        # Solo ventas LOCALES: tras cobrar, mostrar el comprobante (boleta/factura).
        if tipo_venta == 'local':
            return redirect(url_for('comprobante_venta', id_venta=id_venta))
        return redirect(url_for('pedidos'))

    except ValueError as e:
        # Error de negocio (stock insuficiente, cantidad inválida): mensaje claro.
        conn.rollback()
        flash(str(e), 'danger')
        return redirect(url_for('venta_nueva_form'))
    except Exception:
        conn.rollback()
        app.logger.exception("Error al guardar venta local")
        flash('Error al registrar la venta. Intenta de nuevo.', 'danger')
        return redirect(url_for('venta_nueva_form'))
    finally:
        conn.close()


# ── Caja (apertura / cierre / arqueo) ────────────────────────────────────────
# UMBRAL_DIFERENCIA_CAJA: |declarado - sistema| mayor a esto se marca como advertencia
UMBRAL_DIFERENCIA_CAJA = 20.0


@app.route('/caja')
@login_required
@escritura_required
def caja():
    """Panel de caja: estado actual, movimientos del turno, resumen por método
    de pago e historial de cierres.

    REGLA ECONÓMICA DE LA CAJA FÍSICA (integrada desde origin/Lesly):
      EFECTIVO esperado = fondo inicial + ventas locales pagadas en EFECTIVO − egresos
    Yape/Plin y Tarjeta son pagos DIGITALES: se muestran aparte como resumen y
    NO forman parte del dinero físico de la caja (no suben el esperado).
    """
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    caja_abierta = None
    movimientos  = []
    historial = []
    resumen   = {}
    digitales_por_caja = {}
    uids = set()
    try:
        cursor.execute("SELECT * FROM caja WHERE estado = 'abierta' LIMIT 1")
        caja_abierta = cursor.fetchone()
        if caja_abierta:
            cursor.execute("""
                SELECT * FROM movimiento_caja
                WHERE id_caja = %s ORDER BY fecha DESC
            """, (caja_abierta['id_caja'],))
            movimientos = cursor.fetchall()
            uids.add(caja_abierta['id_usuario_apertura'])
            uids.update(m['id_usuario'] for m in movimientos if m['id_usuario'])

        cursor.execute("""
            SELECT * FROM caja ORDER BY fecha_apertura DESC LIMIT 10
        """)
        historial = cursor.fetchall()
        for h in historial:
            if h['id_usuario_apertura']: uids.add(h['id_usuario_apertura'])
            if h['id_usuario_cierre']:   uids.add(h['id_usuario_cierre'])

        # ── Resumen económico por caja (ventas LOCALES con pago confirmado) ──
        caja_ids = ([caja_abierta['id_caja']] if caja_abierta else []) \
                 + [h['id_caja'] for h in historial]
        if caja_ids:
            cursor.execute(f"""
                SELECT p.id_caja, tp.nombre AS metodo,
                       COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
                FROM pago p
                JOIN venta v       ON v.id_venta = p.id_venta
                JOIN tipo_venta tv ON v.id_tipo_venta = tv.id_tipo_venta
                JOIN tipo_pago tp  ON p.id_tipo_pago = tp.id_tipo_pago
                LEFT JOIN detalle_venta d ON d.id_venta = v.id_venta
                WHERE tv.nombre = 'local' AND p.estado = 'confirmado'
                  AND p.id_caja = ANY(%s)
                GROUP BY p.id_caja, tp.nombre
            """, (caja_ids,))
            ventas_por_caja = {}
            for r in cursor.fetchall():
                ventas_por_caja.setdefault(r['id_caja'], {})[r['metodo']] = float(r['total'] or 0)

            cursor.execute("""
                SELECT id_caja, COALESCE(SUM(monto), 0) AS total
                FROM movimiento_caja
                WHERE id_caja = ANY(%s) AND tipo = 'egreso'
                GROUP BY id_caja
            """, (caja_ids,))
            egresos_por_caja = {r['id_caja']: float(r['total'] or 0)
                                for r in cursor.fetchall()}

            for row in ([caja_abierta] if caja_abierta else []) + historial:
                met    = ventas_por_caja.get(row['id_caja'], {})
                fondo  = float(row.get('monto_apertura') or 0)
                ven_ef = met.get('efectivo', 0.0)
                yape   = met.get('yape_plin', 0.0)
                tar    = met.get('tarjeta', 0.0)
                egres  = egresos_por_caja.get(row['id_caja'], 0.0)
                esperado = fondo + ven_ef - egres
                digitales_por_caja[row['id_caja']] = {
                    'yape': yape,
                    'tarjeta': tar,
                    'total_digital': yape + tar,
                    'ventas_efectivo': ven_ef,
                }
                if caja_abierta and row['id_caja'] == caja_abierta['id_caja']:
                    resumen = {
                        'fondo':           fondo,
                        'ventas_efectivo': ven_ef,
                        'yape':            yape,
                        'tarjeta':         tar,
                        'total_ventas':    ven_ef + yape + tar,
                        'egresos':         egres,
                        'esperado':        esperado,
                    }
    except Exception as e:
        app.logger.exception("Error en /caja: %s", e)
    finally:
        conn.close()

    # Nombres de usuarios desde la BD Auth
    nombres = {}
    if uids:
        try:
            conn_a = get_connection_auth()
            cur_a  = conn_a.cursor(dictionary=True)
            cur_a.execute("SELECT id_usuario, nombres FROM usuario WHERE id_usuario = ANY(%s)",
                          (list(uids),))
            nombres = {u['id_usuario']: u['nombres'] for u in cur_a.fetchall()}
            conn_a.close()
        except Exception:
            app.logger.exception("Error consultando nombres de usuarios de caja en la BD Auth")

    return render_template('caja.html',
                           caja=caja_abierta,
                           movimientos=movimientos,
                           resumen=resumen,
                           digitales=digitales_por_caja,
                           historial=historial,
                           nombres=nombres,
                           umbral=UMBRAL_DIFERENCIA_CAJA)


@app.route('/caja/abrir', methods=['POST'])
@login_required
@escritura_required
def caja_abrir():
    """Abre una caja con un monto inicial (solo puede haber UNA abierta)."""
    try:
        monto = float(request.form.get('monto_apertura', '0') or 0)
    except ValueError:
        flash('Monto de apertura inválido.', 'danger')
        return redirect(url_for('caja'))
    if monto < 0:
        flash('El monto de apertura no puede ser negativo.', 'danger')
        return redirect(url_for('caja'))

    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id_caja FROM caja WHERE estado = 'abierta' LIMIT 1")
        if cursor.fetchone():
            flash('Ya existe una caja abierta. Ciérrala antes de abrir otra.', 'danger')
            return redirect(url_for('caja'))

        cursor.execute("""
            INSERT INTO caja (id_usuario_apertura, monto_apertura, estado)
            VALUES (%s, %s, 'abierta')
            RETURNING id_caja
        """, (session.get('usuario_id'), monto))
        id_caja = cursor.fetchone()['id_caja']
        # Movimiento inicial de apertura por el monto de fondo
        # (CHECK de la tabla solo admite 'ingreso'/'egreso'; la apertura es un ingreso)
        cursor.execute("""
            INSERT INTO movimiento_caja (id_caja, tipo, monto, concepto, id_usuario)
            VALUES (%s, 'ingreso', %s, 'Apertura de caja (fondo inicial)', %s)
        """, (id_caja, monto, session.get('usuario_id')))
        conn.commit()
        flash(f'Caja #{id_caja} abierta con S/ {monto:.2f}.', 'success')
    except Exception as e:
        conn.rollback()
        app.logger.exception("Error abriendo caja: %s", e)
        # El índice único parcial idx_caja_una_abierta también protege esto
        flash('No se pudo abrir la caja (¿ya existe una abierta?).', 'danger')
    finally:
        conn.close()
    return redirect(url_for('caja'))


@app.route('/caja/egreso', methods=['POST'])
@login_required
@escritura_required
def caja_egreso():
    """Registra un EGRESO (gasto/salida de efectivo) de la caja abierta.
    Descuenta del efectivo esperado del cierre. (Integrado desde origin/Lesly.)"""
    concepto = request.form.get('concepto', '').strip()
    try:
        monto = float(request.form.get('monto', '0') or 0)
    except ValueError:
        monto = -1
    if not concepto:
        flash('El concepto del egreso es obligatorio.', 'danger')
        return redirect(url_for('caja'))
    if monto <= 0:
        flash('El monto del egreso debe ser mayor que cero.', 'danger')
        return redirect(url_for('caja'))

    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id_caja FROM caja WHERE estado = 'abierta' LIMIT 1")
        caja = cursor.fetchone()
        if not caja:
            flash('No hay ninguna caja abierta. Abre la caja para registrar egresos.', 'danger')
            return redirect(url_for('caja'))

        cursor.execute("""
            INSERT INTO movimiento_caja (id_caja, tipo, monto, concepto, id_usuario)
            VALUES (%s, 'egreso', %s, %s, %s)
        """, (caja['id_caja'], monto, concepto, session.get('usuario_id')))
        conn.commit()
        flash(f'Egreso registrado en la caja #{caja["id_caja"]}: {concepto} por S/ {monto:.2f}.', 'success')
    except Exception:
        conn.rollback()
        app.logger.exception("Error registrando egreso de caja")
        flash('No se pudo registrar el egreso.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('caja'))


@app.route('/caja/cerrar', methods=['POST'])
@login_required
@escritura_required
def caja_cerrar():
    """Cierra la caja abierta: calcula el EFECTIVO esperado
    (fondo + ventas en efectivo − egresos) y registra la diferencia.
    (Separación efectivo/digital integrada desde origin/Lesly.)"""
    try:
        declarado = float(request.form.get('monto_declarado', '0') or 0)
    except ValueError:
        flash('Monto declarado inválido.', 'danger')
        return redirect(url_for('caja'))
    observaciones = request.form.get('observaciones', '').strip() or None

    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM caja WHERE estado = 'abierta' LIMIT 1")
        caja = cursor.fetchone()
        if not caja:
            flash('No hay ninguna caja abierta para cerrar.', 'danger')
            return redirect(url_for('caja'))

        id_caja = caja['id_caja']
        fondo   = float(caja.get('monto_apertura') or 0)

        # Ventas LOCALES pagadas en EFECTIVO (el único dinero físico que entra)
        cursor.execute(f"""
            SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
            FROM pago p
            JOIN venta v       ON v.id_venta = p.id_venta
            JOIN tipo_venta tv ON v.id_tipo_venta = tv.id_tipo_venta
            JOIN tipo_pago tp  ON p.id_tipo_pago = tp.id_tipo_pago
            LEFT JOIN detalle_venta d ON d.id_venta = v.id_venta
            WHERE tv.nombre = 'local' AND p.estado = 'confirmado'
              AND p.id_caja = %s AND tp.nombre = 'efectivo'
        """, (id_caja,))
        ventas_efectivo = float(cursor.fetchone()['total'] or 0)

        # Egresos del turno (descuentan del efectivo esperado)
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0) AS total
            FROM movimiento_caja WHERE id_caja = %s AND tipo = 'egreso'
        """, (id_caja,))
        egresos = float(cursor.fetchone()['total'] or 0)

        esperado = fondo + ventas_efectivo - egresos
        diferencia = declarado - esperado

        # Pagos DIGITALES del turno (resumen informativo; NO afectan el efectivo)
        cursor.execute(f"""
            SELECT tp.nombre AS metodo, COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
            FROM pago p
            JOIN venta v       ON v.id_venta = p.id_venta
            JOIN tipo_venta tv ON v.id_tipo_venta = tv.id_tipo_venta
            JOIN tipo_pago tp  ON p.id_tipo_pago = tp.id_tipo_pago
            LEFT JOIN detalle_venta d ON d.id_venta = v.id_venta
            WHERE tv.nombre = 'local' AND p.estado = 'confirmado'
              AND p.id_caja = %s AND tp.nombre IN ('yape_plin', 'tarjeta')
            GROUP BY tp.nombre
        """, (id_caja,))
        digital = {r['metodo']: float(r['total'] or 0) for r in cursor.fetchall()}
        yape, tarjeta = digital.get('yape_plin', 0.0), digital.get('tarjeta', 0.0)

        # El cierre se refleja solo en la fila de caja (sin movimiento_caja 'cierre')
        cursor.execute("""
            UPDATE caja
            SET estado = 'cerrada',
                id_usuario_cierre = %s,
                fecha_cierre = NOW(),
                monto_cierre_sistema = %s,
                monto_cierre_declarado = %s,
                diferencia = %s,
                observaciones = %s
            WHERE id_caja = %s
        """, (session.get('usuario_id'), esperado, declarado, diferencia,
              observaciones, id_caja))
        conn.commit()

        mensaje = (
            f'Caja #{id_caja} cerrada. EFECTIVO: fondo S/ {fondo:.2f} + ventas '
            f'efectivo S/ {ventas_efectivo:.2f} − egresos S/ {egresos:.2f} '
            f'= esperado S/ {esperado:.2f} | '
            f'PAGOS DIGITALES: Yape/Plin S/ {yape:.2f}, Tarjeta S/ {tarjeta:.2f} | '
            f'Diferencia: S/ {diferencia:+.2f}.'
        )
        if abs(diferencia) > UMBRAL_DIFERENCIA_CAJA:
            flash(mensaje + f' ⚠ La diferencia supera el umbral (S/ {UMBRAL_DIFERENCIA_CAJA:.0f}).', 'warning')
        else:
            flash(mensaje, 'success')
    except Exception:
        conn.rollback()
        app.logger.exception("Error cerrando caja")
        flash('No se pudo cerrar la caja.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('caja'))


@app.route('/pedidos/detalle/<int:id>')
@login_required
@escritura_required
def pedido_detalle(id):
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    pedido     = None
    items      = []
    comprobante = None
    try:
        # Consulta 1 (Tienda): venta + total
        cursor.execute(f"""
            SELECT
                v.*,
                ev.nombre AS estado,
                tv.nombre AS tipo_venta,
                COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
            FROM venta v
            LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
            LEFT JOIN tipo_venta tv ON v.id_tipo_venta = tv.id_tipo_venta
            LEFT JOIN detalle_venta d ON v.id_venta = d.id_venta
            WHERE v.id_venta = %s
            GROUP BY v.id_venta, ev.nombre, tv.nombre
        """, (id,))
        pedido = cursor.fetchone()
        if pedido:
            pedido['id_venta'] = pedido.get('id_venta', id)
            # Consulta 2 (Auth): cliente y vendedor responsable
            conn_a   = get_connection_auth()
            cursor_a = conn_a.cursor(dictionary=True)
            if pedido.get('id_cliente'):
                cursor_a.execute(
                    "SELECT nombre, apellido, telefono, nro_documento, direccion FROM cliente WHERE id_cliente = %s",
                    (pedido['id_cliente'],))
                cli = cursor_a.fetchone() or {}
                pedido['cliente_nombre']    = (f"{cli.get('nombre') or ''} {cli.get('apellido') or ''}").strip()
                pedido['cliente_telefono']  = cli.get('telefono')
                pedido['cliente_documento'] = (cli.get('nro_documento') or '').strip() or None
                pedido['cliente_direccion'] = (cli.get('direccion') or '').strip() or None
            if pedido.get('id_usuario_vendedor'):
                cursor_a.execute(
                    "SELECT nombres FROM usuario WHERE id_usuario = %s",
                    (pedido['id_usuario_vendedor'],))
                usr = cursor_a.fetchone()
                pedido['responsable'] = usr['nombres'] if usr else None
            conn_a.close()
        cursor.execute(f"""
            SELECT d.*, p.nombre AS producto_nombre,
                   (d.precio_unitario - d.descuento_unitario) AS precio_final,
                   ROUND({SQL_IMPORTE_LINEA.format(p='d.')}, 2) AS importe
            FROM detalle_venta d
            LEFT JOIN producto p ON d.id_producto = p.id_producto
            WHERE d.id_venta = %s
        """, (id,))
        items = cursor.fetchall()
        try:
            cursor.execute("SELECT * FROM comprobante WHERE id_venta = %s LIMIT 1", (id,))
            comprobante = cursor.fetchone()
        except Exception:
            comprobante = None
    except Exception:
        app.logger.exception("Error pedido_detalle %s", id)
    finally:
        conn.close()
    # Fases válidas leídas de la BD (misma fuente que el panel admin y el
    # cliente), para que el timeline siempre muestre nombres consistentes.
    PASOS = []
    try:
        conn_f   = get_connection_tienda()
        cursor_f = conn_f.cursor(dictionary=True)
        cursor_f.execute("SELECT nombre FROM estado_venta WHERE activo = TRUE ORDER BY orden")
        PASOS = [r['nombre'] for r in cursor_f.fetchall()]
        conn_f.close()
    except Exception:
        PASOS = []
    PASOS = [f for f in PASOS if f != 'cancelado']
    if not PASOS:
        PASOS = ['pendiente', 'procesando', 'enviado', 'entregado']
    # Calcular idx_actual en Python para evitar el error .index() en Jinja2
    estado_actual = (pedido.get('estado') or 'pendiente') if pedido else 'pendiente'
    idx_actual = PASOS.index(estado_actual) if estado_actual in PASOS else 0
    return render_template('pedido_detalle.html', pedido=pedido, items=items,
                           comprobante=comprobante, id=id,
                           idx_actual=idx_actual, fases_orden=PASOS,
                           estados=PASOS, transiciones=TRANSICIONES_PEDIDO)

@app.route('/ventas/<int:id_venta>/comprobante')
@login_required
@escritura_required   # mismo control que ventas/caja: solo administrador y vendedor
def comprobante_venta(id_venta):
    """Muestra el comprobante (boleta/factura SIMULADA) de una venta.

    SOLO LECTURA: si la venta aún no tiene comprobante, avisa que se emite
    al confirmar el pago (D1). Nunca crea ni escribe en un GET.
    """
    formato = 'ticket' if request.args.get('formato') == 'ticket' else 'a4'
    resultado = _datos_comprobante(id_venta)
    if resultado is None:
        abort(404)
    if resultado['render'] is None:
        flash('El comprobante aún no se emitió: se emite al confirmar el pago.', 'info')
        return redirect(url_for('pedido_detalle', id=id_venta))
    return render_template('comprobante/comprobante_venta.html',
                           formato=formato, es_cliente=False,
                           **resultado['render'])


def _datos_comprobante(id_venta):
    """Carga TODO lo necesario para renderizar un comprobante (solo SELECT).

    Devuelve None si la venta no existe, {'venta':..., 'render': None} si
    la venta no tiene comprobante aún, o {'venta':..., 'render': {...}}.
    """
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    venta = comprobante = None
    items = []
    metodo_pago = None
    try:
        cursor.execute("""
            SELECT v.*, tv.nombre AS tipo_venta, ev.nombre AS estado_venta
            FROM venta v
            LEFT JOIN tipo_venta tv   ON v.id_tipo_venta   = tv.id_tipo_venta
            LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
            WHERE v.id_venta = %s
        """, (id_venta,))
        venta = cursor.fetchone()
        if not venta:
            return None

        # Ítems con el descuento congelado: importe NETO por línea con la
        # fórmula única; precio_final es lo que pagó el cliente por unidad.
        cursor.execute(f"""
            SELECT d.id_detalle, d.cantidad, d.precio_unitario,
                   d.descuento_unitario,
                   (d.precio_unitario - d.descuento_unitario) AS precio_final,
                   COALESCE(d.nombre_producto, p.nombre) AS descripcion,
                   ROUND({SQL_IMPORTE_LINEA.format(p='d.')}, 2) AS importe
            FROM detalle_venta d
            LEFT JOIN producto p ON d.id_producto = p.id_producto
            WHERE d.id_venta = %s
            ORDER BY d.id_detalle
        """, (id_venta,))
        items = cursor.fetchall()
        # Descuento total aplicado en la venta (fila informativa del
        # comprobante: solo se muestra si es > 0)
        total_descuento = round(sum(
            float(i['descuento_unitario'] or 0) * int(i['cantidad'] or 1)
            for i in items), 2)

        # Método de pago registrado (efectivo / tarjeta / yape_plin)
        cursor.execute("""
            SELECT tp.nombre FROM pago p
            JOIN tipo_pago tp ON p.id_tipo_pago = tp.id_tipo_pago
            WHERE p.id_venta = %s LIMIT 1
        """, (id_venta,))
        fila_pago = cursor.fetchone()
        metodo_pago = fila_pago['nombre'] if fila_pago else None

        cursor.execute("""
            SELECT c.*, tc.nombre AS tipo
            FROM comprobante c
            LEFT JOIN tipo_comprobante tc ON c.id_tipo_comprobante = tc.id_tipo_comprobante
            WHERE c.id_venta = %s LIMIT 1
        """, (id_venta,))
        comprobante = cursor.fetchone()
    finally:
        conn.close()

    if not comprobante:
        return {'venta': venta, 'render': None}

    # Datos en la BD Auth: cliente (DNI/nombre) y vendedor (solo lectura).
    cliente_doc = cliente_nombre = vendedor = None
    try:
        conn_a   = get_connection_auth()
        cursor_a = conn_a.cursor(dictionary=True)
        if venta.get('id_cliente'):
            cursor_a.execute(
                "SELECT nombre, apellido, nro_documento FROM cliente WHERE id_cliente = %s",
                (venta['id_cliente'],))
            cli = cursor_a.fetchone() or {}
            cliente_doc    = (cli.get('nro_documento') or '').strip() or None
            cliente_nombre = (f"{cli.get('nombre') or ''} {cli.get('apellido') or ''}").strip() or None
        if venta.get('id_usuario_vendedor'):
            cursor_a.execute(
                "SELECT nombres FROM usuario WHERE id_usuario = %s",
                (venta['id_usuario_vendedor'],))
            usr = cursor_a.fetchone()
            vendedor = usr['nombres'] if usr else None
        conn_a.close()
    except Exception:
        app.logger.exception("Error consultando datos de Auth para el comprobante %s", id_venta)

    tipo = (comprobante.get('tipo') or 'boleta').lower()
    serie  = comprobante.get('serie') or ('F001' if tipo == 'factura' else 'B001')
    numero = comprobante.get('numero') or ''
    dig    = numero[-6:] if numero else '000000'
    serie_numero = f'{serie}-{dig}'

    # Totales: de la fila del comprobante; si es una fila vieja sin
    # totales, se calculan para MOSTRAR (sin escribir en el GET).
    total    = round(float(comprobante.get('total') or 0), 2)
    subtotal = round(float(comprobante.get('subtotal') or 0), 2)
    igv      = round(float(comprobante.get('igv') or 0), 2)
    if not total and items:
        total    = round(sum(float(i['importe'] or 0) for i in items), 2)
        subtotal = round(total / 1.18, 2)
        igv      = round(total - subtotal, 2)
    total_letras = numero_a_letras(total)

    # QR con el RUC del emisor desde el .env (EMISOR de services/comprobantes)
    qr_texto = texto_qr(tipo, serie, dig, igv, total,
                        comprobante.get('fecha_emision') or '')
    qr_data = _qr_data_uri(qr_texto)

    render = {
        'venta': venta, 'comprobante': comprobante, 'items': items,
        'cliente_doc': cliente_doc, 'cliente_nombre': cliente_nombre,
        'vendedor': vendedor, 'metodo_pago': metodo_pago,
        'subtotal': subtotal, 'igv': igv, 'total': total,
        'descuento': total_descuento,
        'total_letras': total_letras, 'serie_numero': serie_numero,
        'qr_data': qr_data, 'emisor': EMISOR,
    }
    return {'venta': venta, 'render': render}


@app.route('/mi_comprobante/<int:id_venta>')
@login_required
def mi_comprobante(id_venta):
    """Comprobante del CLIENTE: solo el dueño de la venta (ajeno -> 403).

    Solo lectura (GET sin escrituras); si la venta aún no tiene comprobante,
    avisa que se emite al confirmar el pago.
    """
    resultado = _datos_comprobante(id_venta)
    if resultado is None:
        abort(404)
    venta = resultado['venta']
    if session.get('rol') != 'cliente' or venta['id_cliente'] != session.get('usuario_id'):
        abort(403)
    if resultado['render'] is None:
        flash('Tu comprobante se emitirá cuando se confirme el pago.', 'info')
        return redirect(url_for('mis_pedidos'))
    formato = 'ticket' if request.args.get('formato') == 'ticket' else 'a4'
    return render_template('comprobante/comprobante_venta.html',
                           formato=formato, es_cliente=True,
                           **resultado['render'])


@app.route('/mis_comprobantes')
@login_required
def mis_comprobantes():
    """Historial de comprobantes del cliente (solo los suyos; solo lectura)."""
    if session.get('rol') != 'cliente':
        return redirect(url_for('pedidos'))
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        # P4: COUNT del conjunto del CLIENTE (no de la página) y página
        # con ORDER BY estable (id de desempate); el LIMIT 100 fijo se va.
        cursor.execute("SELECT COUNT(*) AS total FROM venta v"
                       " JOIN comprobante c ON c.id_venta = v.id_venta"
                       " WHERE v.id_cliente = %s", (session['usuario_id'],))
        total = cursor.fetchone()['total']
        pagina = _pagina_actual(total, POR_PAGINA_CLIENTE)
        cursor.execute(f"""
            SELECT c.serie, c.numero, c.total, c.fecha_emision, c.estado_sunat,
                   tc.nombre AS tipo, v.id_venta,
                   {SQL_PARED_LIMA.format(p='v.', col='fecha')} AS fecha_venta
              FROM venta v
              JOIN comprobante c ON c.id_venta = v.id_venta
              LEFT JOIN tipo_comprobante tc ON tc.id_tipo_comprobante = c.id_tipo_comprobante
             WHERE v.id_cliente = %s
             ORDER BY c.fecha_emision DESC, c.id_comprobante DESC
             LIMIT %s OFFSET %s
        """, (session['usuario_id'], POR_PAGINA_CLIENTE,
              (pagina - 1) * POR_PAGINA_CLIENTE))
        comprobantes = cursor.fetchall()
    finally:
        conn.close()
    return render_template('mis_comprobantes.html', comprobantes=comprobantes,
                           pagina=pagina, total=total,
                           por_pagina=POR_PAGINA_CLIENTE,
                           filtros_qs=_filtros_sin_pagina())


# Criterio del alcance (§6.9): solo cuentan como vendidas las ventas con pago
# CONFIRMADO (uq_pago_venta garantiza un único pago por venta: JOIN directo).
SQL_VENTAS_HOY_PAGADAS = f"""
    SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS ventas_hoy
    FROM venta v
    JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
    JOIN detalle_venta d ON d.id_venta = v.id_venta
    WHERE (v.fecha AT TIME ZONE 'America/Lima')::date = (NOW() AT TIME ZONE 'America/Lima')::date
"""

# Sintaxis PostgreSQL: DATE_SUB es MySQL y fallaba aquí, dejando la sección
# "Resultados" (movimientos de los últimos 30 días) siempre vacía.
SQL_MOVIMIENTOS_30DIAS = """
    SELECT tipo,
           COALESCE(SUM(cantidad),0) AS total_cantidad,
           COALESCE(SUM(precio_unitario * cantidad),0) AS total_valor
    FROM inventario_movimiento
    WHERE fecha >= NOW() - INTERVAL '30 days'
    GROUP BY tipo
"""


@app.route('/reportes')
@login_required
@escritura_required
def reportes():
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    resumen = {}
    reportes_data = None
    top_unidades = top_monto = None

    # RF12: productos más vendidos por rango de fechas — SOLO pagos
    # confirmados (criterio §6.9), días calendario Lima, parametrizado.
    # Rango vacío = todo el historial; top 10 por unidades y por monto.
    rango_sql, rango_params = "", []
    for campo in ('desde', 'hasta'):
        valor = (request.args.get(campo) or '').strip()
        if not valor:
            continue
        try:
            fecha = datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            flash('Fecha inválida, rango ignorado.', 'warning')
            continue
        operador = '>=' if campo == 'desde' else '<='
        rango_sql += f" AND {SQL_DIA_LIMA.format(p='v.', col='fecha')} {operador} %s"
        rango_params.append(fecha)
    if len(rango_params) == 2 and rango_params[0] > rango_params[1]:
        flash('Rango inválido: «desde» es posterior a «hasta».', 'warning')
        rango_sql, rango_params = "", []
    sql_top = f"""
        SELECT p.id_producto, p.nombre,
               SUM(d.cantidad) AS unidades,
               SUM({SQL_IMPORTE_LINEA.format(p='d.')}) AS monto
          FROM detalle_venta d
          JOIN venta v ON v.id_venta = d.id_venta
          JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
          JOIN producto p ON p.id_producto = d.id_producto
         WHERE 1=1{rango_sql}
         GROUP BY p.id_producto, p.nombre
    """

    try:
        # Ventas de hoy SOLO con pago confirmado (criterio del alcance §6.9).
        try:
            cursor.execute(SQL_VENTAS_HOY_PAGADAS)
            row = cursor.fetchone()
            resumen['ventas_hoy'] = float(row['ventas_hoy'] or 0)
        except Exception:
            resumen['ventas_hoy'] = 0

        # Stock total (suma de stock de productos)
        try:
            cursor.execute("SELECT COALESCE(SUM(stock),0) AS stock_total FROM producto")
            row = cursor.fetchone()
            resumen['stock_total'] = int(row['stock_total'] or 0)
        except Exception:
            resumen['stock_total'] = 0

        # Alertas pendientes
        try:
            cursor.execute("SELECT COUNT(*) AS cnt FROM alerta WHERE resuelta = FALSE")
            row = cursor.fetchone()
            resumen['alertas'] = int(row['cnt'] or 0)
        except Exception:
            resumen['alertas'] = 0

        # Dashboard inventario: entradas/salidas últimos 30 días (cantidad y valor)
        try:
            cursor.execute(SQL_MOVIMIENTOS_30DIAS)
            agg = cursor.fetchall()
            # Construir filas para la tabla de reportes
            headers = ['Tipo', 'Cantidad (30d)', 'Valor (30d)']
            rows = []
            entradas = salidas = 0
            entradas_val = salidas_val = 0.0
            for a in agg:
                tipo = a.get('tipo') or '–'
                qty  = int(a.get('total_cantidad') or 0)
                val  = float(a.get('total_valor') or 0.0)
                rows.append([tipo, qty, f"{val:.2f}"])
                if tipo == 'entrada':
                    entradas += qty; entradas_val += val
                elif tipo == 'salida':
                    salidas += qty; salidas_val += val
            reportes_data = {
                'headers': headers,
                'rows': rows,
                'entradas_30d': entradas,
                'salidas_30d': salidas,
                'entradas_val_30d': entradas_val,
                'salidas_val_30d': salidas_val
            }
        except Exception:
            reportes_data = None

        # RF12: los dos rankings (mismo WHERE, órdenes distintos, id desempate)
        cursor.execute(sql_top + " ORDER BY unidades DESC, p.id_producto ASC LIMIT 10",
                       rango_params)
        top_unidades = cursor.fetchall()
        cursor.execute(sql_top + " ORDER BY monto DESC, p.id_producto ASC LIMIT 10",
                       rango_params)
        top_monto = cursor.fetchall()

    finally:
        conn.close()

    return render_template('reportes.html', resumen=resumen, reportes=reportes_data,
                           top_unidades=top_unidades, top_monto=top_monto)


# ── Alertas ─────────────────────────────────────────────────
@app.route('/alertas')
@login_required
@escritura_required
def alertas():
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    alertas = []
    bajo_stock = []
    try:
        cursor.execute("""
            SELECT id_producto, nombre, stock, precio
            FROM producto WHERE stock < 15 ORDER BY stock ASC
        """)
        bajo_stock = cursor.fetchall()
        # Sincronizar: crear alerta en BD para cada producto con stock < 15 si no existe ya una sin resolver
        # (esquema real: tabla alerta con id_producto + id_tipo_alerta; 1 = stock_bajo)
        for p in bajo_stock:
            cursor.execute("""
                SELECT id_alerta FROM alerta
                WHERE id_tipo_alerta = 1 AND id_producto = %s AND resuelta = FALSE
                LIMIT 1
            """, (p['id_producto'],))
            if not cursor.fetchone():
                prioridad = 'alta' if p['stock'] < 5 else 'media'
                cursor.execute("""
                    INSERT INTO alerta (id_tipo_alerta, id_producto, descripcion, prioridad, fecha)
                    VALUES (1, %s, %s, %s, NOW())
                """, (
                    p['id_producto'],
                    f"Stock insuficiente: {p['nombre']} tiene {p['stock']} unidades (mínimo sugerido: 15)",
                    prioridad
                ))
        # Limpiar alertas stale: resolver aquellas cuyo producto ya no tiene stock < 15
        cursor.execute("""
            UPDATE alerta SET resuelta = TRUE
            WHERE id_tipo_alerta = 1 AND resuelta = FALSE AND id_producto NOT IN (SELECT id_producto FROM producto WHERE stock < 15)
        """)
        conn.commit()
        cursor.execute("SELECT * FROM alerta ORDER BY fecha DESC LIMIT 200")
        alertas = cursor.fetchall()
    except Exception:
        alertas = []
        conn.rollback()
    finally:
        conn.close()
    return render_template('alertas.html', alertas=alertas, bajo_stock=bajo_stock)


@app.route('/alertas/resolver/<int:id>', methods=['POST'])
@login_required
@escritura_required
def alertas_resolver(id):
    conn = get_connection_tienda()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE alerta SET resuelta = TRUE WHERE id_alerta = %s", (id,))
        conn.commit()
        flash("Alerta marcada como resuelta.", "success")
    except Exception as e:
        conn.rollback()
        flash("Error al resolver alerta.", "danger")
        app.logger.exception("Error resolviendo alerta %s: %s", id, e)
    finally:
        conn.close()
    return redirect(url_for('alertas'))


# ── Panel admin de ofertas (Fase 2.3) ───────────────────────────
# Reglas: el precio final lo calcula SIEMPRE el servidor; la BD garantiza
# una sola oferta ACTIVA por producto y rango (ex_oferta_vigencia) y un
# precio_fijo menor al precio del producto (trigger trg_oferta_validar_precio).
# No hay DELETE: solo activar/desactivar. Toda escritura queda en auditoria
# con valor anterior y nuevo, y los POST validan CSRF (_CSRF_PROTEGIDAS).

def _ts_lima(dt):
    """Datetime naive (hora Lima) -> texto timestamptz con offset -05 explícito.

    Perú no tiene horario de verano: el offset fijo evita depender del
    TimeZone de la sesión de PostgreSQL al insertar la vigencia.
    """
    return dt.strftime('%Y-%m-%d %H:%M:00-05')


def _validar_form_oferta():
    """Lee y valida el form de alta/edición de una oferta.

    Devuelve (datos, None) si todo cuadra o (None, mensaje) si hay error.
    """
    try:
        id_producto = int(request.form.get('id_producto') or 0)
    except ValueError:
        return None, 'Producto inválido.'
    tipo = (request.form.get('tipo') or '').strip()
    try:
        valor = Decimal((request.form.get('valor') or '').strip())
    except InvalidOperation:
        return None, 'El valor del descuento debe ser un número.'
    try:
        inicio = datetime.strptime((request.form.get('fecha_inicio') or '').strip(),
                                   '%Y-%m-%dT%H:%M')
        fin = datetime.strptime((request.form.get('fecha_fin') or '').strip(),
                                '%Y-%m-%dT%H:%M')
    except ValueError:
        return None, 'Fechas de vigencia inválidas.'
    if id_producto <= 0:
        return None, 'Selecciona un producto.'
    if tipo not in ('porcentaje', 'precio_fijo'):
        return None, 'Tipo de descuento inválido.'
    if valor <= 0:
        return None, 'El valor debe ser mayor a cero.'
    if tipo == 'porcentaje' and valor > 100:
        return None, 'El porcentaje no puede superar 100.'
    if fin <= inicio:
        return None, 'La fecha fin debe ser posterior a la fecha inicio.'
    return {'id_producto': id_producto, 'tipo': tipo, 'valor': valor,
            'inicio': inicio, 'fin': fin}, None


def _flash_error_oferta(e):
    """Traduce los errores de integridad de la BD a mensajes claros."""
    msg = str(e)
    if isinstance(e, _pg_errores.ExclusionViolation) or 'ex_oferta_vigencia' in msg:
        flash('Ya existe una oferta ACTIVA que solapa esas fechas para este '
              'producto. Cambia la vigencia o desactiva la otra oferta primero.',
              'danger')
    elif 'oferta invalida' in msg:
        flash('Oferta rechazada: el precio fijo debe ser MENOR al precio '
              'actual del producto.', 'danger')
    else:
        app.logger.warning("Oferta rechazada por la BD: %s", e)
        flash('No se pudo guardar la oferta. Revisa los datos e inténtalo de '
              'nuevo.', 'danger')


def _auditar_oferta(cursor, id_oferta, accion, anterior, nuevo):
    """Auditoría de oferta con valor ANTERIOR y NUEVO (JSON neutro)."""
    cursor.execute("""
        INSERT INTO auditoria
            (id_usuario, tabla_afectada, id_registro, accion,
             valor_anterior, valor_nuevo)
        VALUES (%s, 'oferta', %s, %s, %s, %s)
    """, (session.get('usuario_id'), id_oferta, accion,
          json.dumps(anterior, ensure_ascii=False, default=str)
              if anterior is not None else None,
          json.dumps(nuevo, ensure_ascii=False, default=str)
              if nuevo is not None else None))


def _producto_de_oferta(cursor, datos):
    """Producto ACTIVO destino de la oferta, o None (con flash ya puesto)."""
    cursor.execute("SELECT nombre, precio FROM producto"
                   " WHERE id_producto = %s AND activo = TRUE",
                   (datos['id_producto'],))
    prod = cursor.fetchone()
    if not prod:
        flash('El producto ya no existe o está inactivo.', 'danger')
    return prod


def _precio_fijo_invalido(prod, datos):
    """True (con flash) si el precio_fijo no es menor al precio del producto.

    La BD lo vuelve a impedir con el trigger trg_oferta_validar_precio;
    este chequeo es solo para dar un mensaje claro ANTES de fallar.
    """
    if datos['tipo'] == 'precio_fijo' and datos['valor'] >= prod['precio']:
        flash(f'Oferta rechazada: el precio fijo (S/ {datos["valor"]:.2f}) debe '
              f'ser menor al precio de "{prod["nombre"]}" '
              f'(S/ {prod["precio"]:.2f}).', 'danger')
        return True
    return False


@app.route('/admin/ofertas')
@login_required
@escritura_required
def admin_ofertas():
    """Listado de ofertas con estado: vigente / futura / vencida / inactiva."""
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    ofertas, productos = [], []
    try:
        cursor.execute(f"""
            SELECT o.id_oferta, o.id_producto, o.tipo, o.valor, o.activa,
                   {SQL_PARED_LIMA.format(p='o.', col='fecha_inicio')} AS fecha_inicio,
                   {SQL_PARED_LIMA.format(p='o.', col='fecha_fin')} AS fecha_fin,
                   p.nombre AS producto, p.precio,
                   CASE
                       WHEN NOT o.activa THEN 'inactiva'
                       WHEN NOW() < o.fecha_inicio THEN 'futura'
                       WHEN NOW() >= o.fecha_fin THEN 'vencida'
                       ELSE 'vigente'
                   END AS estado
              FROM oferta o
              JOIN producto p ON p.id_producto = o.id_producto
             ORDER BY o.activa DESC, o.fecha_inicio DESC, o.id_oferta DESC
        """)
        ofertas = cursor.fetchall()
        # El descuento lo calcula el servidor con la MISMA función del
        # checkout (redondeo y clamp), nunca el navegador.
        for o in ofertas:
            o['descuento_unit'] = calcular_descuento_unitario(
                o['precio'], o['tipo'], o['valor'])
            o['precio_final'] = Decimal(o['precio']) - o['descuento_unit']
        cursor.execute("SELECT id_producto, nombre, precio FROM producto"
                       " WHERE activo = TRUE ORDER BY nombre")
        productos = cursor.fetchall()
    finally:
        conn.close()
    return render_template('admin_ofertas.html', ofertas=ofertas,
                           productos=productos, seccion_activa='ofertas')


@app.route('/admin/ofertas/nueva', methods=['POST'])
@login_required
@escritura_required
def admin_ofertas_nueva():
    datos, error = _validar_form_oferta()
    if error:
        flash(error, 'danger')
        return redirect(url_for('admin_ofertas'))
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        prod = _producto_de_oferta(cursor, datos)
        if prod is None or _precio_fijo_invalido(prod, datos):
            return redirect(url_for('admin_ofertas'))
        cursor.execute("""
            INSERT INTO oferta
                (id_producto, tipo, valor, fecha_inicio, fecha_fin, activa, creado_por)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s)
            RETURNING id_oferta
        """, (datos['id_producto'], datos['tipo'], datos['valor'],
              _ts_lima(datos['inicio']), _ts_lima(datos['fin']),
              session.get('usuario_id')))
        id_oferta = cursor.fetchone()['id_oferta']
        _auditar_oferta(cursor, id_oferta, 'crear', None,
                        {**datos, 'id_oferta': id_oferta})
        conn.commit()
        flash('Oferta creada correctamente.', 'success')
    except Exception as e:
        conn.rollback()
        _flash_error_oferta(e)
    finally:
        conn.close()
    return redirect(url_for('admin_ofertas'))


@app.route('/admin/ofertas/editar/<int:id>', methods=['POST'])
@login_required
@escritura_required
def admin_ofertas_editar(id):
    datos, error = _validar_form_oferta()
    if error:
        flash(error, 'danger')
        return redirect(url_for('admin_ofertas'))
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM oferta WHERE id_oferta = %s FOR UPDATE",
                       (id,))
        anterior = cursor.fetchone()
        if not anterior:
            flash('La oferta no existe.', 'danger')
            return redirect(url_for('admin_ofertas'))
        prod = _producto_de_oferta(cursor, datos)
        if prod is None or _precio_fijo_invalido(prod, datos):
            return redirect(url_for('admin_ofertas'))
        cursor.execute("""
            UPDATE oferta
               SET id_producto = %s, tipo = %s, valor = %s,
                   fecha_inicio = %s, fecha_fin = %s
             WHERE id_oferta = %s
            RETURNING *
        """, (datos['id_producto'], datos['tipo'], datos['valor'],
              _ts_lima(datos['inicio']), _ts_lima(datos['fin']), id))
        nuevo = cursor.fetchone()
        _auditar_oferta(cursor, id, 'editar', dict(anterior), dict(nuevo))
        cursor.execute("SELECT EXISTS (SELECT 1 FROM detalle_venta"
                       " WHERE id_oferta = %s) AS usada", (id,))
        usada = bool(cursor.fetchone()['usada'])
        conn.commit()
        flash('Oferta actualizada correctamente.', 'success')
        if usada:
            flash('Esta oferta ya se aplicó a ventas anteriores: esas ventas '
                  'ya hechas no cambian (el descuento quedó congelado en cada '
                  'venta).', 'info')
    except Exception as e:
        conn.rollback()
        _flash_error_oferta(e)
    finally:
        conn.close()
    return redirect(url_for('admin_ofertas'))


def _toggle_oferta(id, activa):
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM oferta WHERE id_oferta = %s FOR UPDATE",
                       (id,))
        anterior = cursor.fetchone()
        if not anterior:
            flash('La oferta no existe.', 'danger')
            return redirect(url_for('admin_ofertas'))
        if bool(anterior['activa']) == activa:
            flash('La oferta ya estaba %s.'
                  % ('activa' if activa else 'inactiva'), 'info')
            return redirect(url_for('admin_ofertas'))
        cursor.execute("UPDATE oferta SET activa = %s WHERE id_oferta = %s"
                       " RETURNING *", (activa, id))
        nuevo = cursor.fetchone()
        _auditar_oferta(cursor, id,
                        'activar' if activa else 'desactivar',
                        dict(anterior), dict(nuevo))
        usada = False
        if not activa:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM detalle_venta"
                           " WHERE id_oferta = %s) AS usada", (id,))
            usada = bool(cursor.fetchone()['usada'])
        conn.commit()
        flash('Oferta %s correctamente.'
              % ('activada' if activa else 'desactivada'), 'success')
        if usada:
            flash('Esta oferta ya se aplicó a ventas anteriores: esas ventas '
                  'ya hechas no cambian (el descuento quedó congelado en cada '
                  'venta).', 'info')
    except Exception as e:
        conn.rollback()
        _flash_error_oferta(e)
    finally:
        conn.close()
    return redirect(url_for('admin_ofertas'))


@app.route('/admin/ofertas/activar/<int:id>', methods=['POST'])
@login_required
@escritura_required
def admin_ofertas_activar(id):
    return _toggle_oferta(id, True)


@app.route('/admin/ofertas/desactivar/<int:id>', methods=['POST'])
@login_required
@escritura_required
def admin_ofertas_desactivar(id):
    return _toggle_oferta(id, False)


# ── Carrito cliente ────────────────────────────────────────────
@app.route('/carrito')
@login_required
def carrito():
    if session.get('rol') != 'cliente':
        return redirect(url_for('productos'))
    return render_template('carrito.html')


@app.route('/carrito/agregar', methods=['POST'])
@login_required
def carrito_agregar():
    """Valida un producto contra la BD antes de agregarlo al carrito.

    Fuente única de verdad del carrito: localStorage del navegador.
    Este endpoint ya NO guarda nada en sesión; solo verifica que el
    producto exista, esté activo y con stock, y devuelve nombre/precio
    OFICIALES de la BD — incluidos el descuento de la oferta vigente y
    el precio final — para que el frontend corrija datos obsoletos.
    El navegador nunca decide el precio: solo renderiza estos valores.
    """
    data        = request.get_json(silent=True) or {}
    id_producto = int(data.get('id_producto', 0))
    cantidad    = int(data.get('cantidad', 1))

    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"""
        SELECT p.nombre, p.precio, p.stock,
               COALESCE(of.descuento_unitario, 0) AS descuento,
               p.precio - COALESCE(of.descuento_unitario, 0) AS precio_final
          FROM producto p
          {SQL_PRECIO_VIGENTE.format(p="p.", a="of")}
         WHERE p.id_producto=%s AND p.activo = TRUE
    """, (id_producto,))
    prod = cursor.fetchone()
    conn.close()

    if not prod:
        return {'ok': False,
                'error': 'Este producto ya no está disponible. Se quitará del carrito.'}
    if prod['stock'] is not None and int(prod['stock']) < 1:
        return {'ok': False,
                'error': f"'{prod['nombre']}' está agotado. Se quitará del carrito."}
    if int(prod['stock']) < cantidad:
        return {'ok': False,
                'error': f"Stock insuficiente de '{prod['nombre']}': quedan {prod['stock']}."}

    return {'ok': True,
            'id_producto': id_producto,
            'nombre':  prod['nombre'],
            'precio':  float(prod['precio']),           # base (referencia)
            'descuento': float(prod['descuento']),       # dcto unitario vigente
            'precio_final': float(prod['precio_final']), # lo que se paga
            'stock':   int(prod['stock'])}


@app.route('/carrito/precios', methods=['POST'])
@login_required
def carrito_precios():
    """Precios vigentes por LOTE para refrescar el carrito al cargarlo.

    Devuelve, por cada id pedido, precio base, descuento de la oferta
    vigente y precio final — la MISMA fuente (SQL_PRECIO_VIGENTE) que
    usa el checkout. Solo lectura: el navegador renderiza estos valores,
    jamás los decide; el total oficial lo recalcula el servidor al
    confirmar el pedido.
    """
    data = request.get_json(silent=True) or {}
    try:
        ids = [int(i) for i in (data.get('ids') or [])]
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'ids inválidos'}, 400
    if len(ids) > 100:
        return {'ok': False,
                'error': 'Demasiados productos en el carrito: el máximo por '
                         'lote es 100.'}, 400
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        lote = precios_vigentes(cursor, ids)
    finally:
        conn.close()
    precios = {idp: {'precio': float(f['precio']),
                     'descuento': float(f['descuento_unitario'] or 0),
                     'precio_final': float(f['precio_final'])}
               for idp, f in lote.items()}
    return {'ok': True, 'precios': precios}


# ── Cambiar clave ─────────────────────────────────────────────
@app.route('/cambiar_clave', methods=['GET', 'POST'])
@login_required
def cambiar_clave():
    if request.method == 'POST':
        nueva     = request.form['nueva']
        confirmar = request.form['confirmar']
        if nueva != confirmar:
            return render_template('cambiar_clave.html', error='Las contraseñas no coinciden')
        nueva_hash = bcrypt.generate_password_hash(nueva).decode('utf-8')
        conn   = get_connection_auth()
        cursor = conn.cursor()
        cursor.execute("UPDATE usuario SET contrasena = %s WHERE id_usuario = %s",
                       (nueva_hash, session['usuario_id']))
        conn.commit()
        conn.close()
        flash("Contraseña actualizada correctamente.", "success")
        return redirect(url_for('productos'))
    return render_template('cambiar_clave.html')



# ═══════════════════════════════════════════════════════════════════
# NUEVAS RUTAS – Registro cliente, pedido aceptado, fases, comprobante
# ═══════════════════════════════════════════════════════════════════

def _correo_html_codigo(nombre, codigo, motivo):
    """HTML estándar (marca CLEOFERR) para correos que contienen un código."""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;border:1px solid #ddd;border-radius:12px;overflow:hidden">
      <div style="background:linear-gradient(135deg,#1A1A2E,#4682B4);padding:24px;text-align:center">
        <h2 style="color:white;margin:0;font-size:1.4rem">🔧 Ferretería Inversiones CLEOFERR</h2>
      </div>
      <div style="padding:28px">
        <p style="color:#333;font-size:1rem">Hola <strong>{nombre}</strong>,</p>
        <p style="color:#555">{motivo}</p>
        <div style="text-align:center;margin:24px 0">
          <span style="font-size:2.8rem;font-weight:900;letter-spacing:12px;color:#4682B4;
                       background:#f0f6ff;padding:16px 24px;border-radius:12px;
                       display:inline-block;border:2px dashed #4682B4">{codigo}</span>
        </div>
        <p style="color:#888;font-size:0.85rem;text-align:center">
          ⏱ Este código es válido por <strong>10 minutos</strong>.
        </p>
        <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
        <p style="color:#aaa;font-size:0.78rem;text-align:center">
          Si no solicitaste este código, puedes ignorar este mensaje.
        </p>
      </div>
    </div>
    """


@app.route('/registro_cliente', methods=['POST'])
def registro_cliente():
    """Registro de nuevo cliente.

    Crea (o reutiliza) la cuenta con activo=FALSE y guarda SOLO el hash del
    codigo OTP en verificacion_cuenta (BD Auth). El codigo viaja unicamente
    por correo: nunca se muestra en pantalla.
    """
    import random

    nombre     = request.form.get('nombre', '').strip()
    apellido   = request.form.get('apellido', '').strip()
    correo     = _normalizar_correo(request.form.get('correo'))
    telefono   = request.form.get('telefono', '').strip()
    direccion  = request.form.get('direccion', '').strip()
    contrasena = request.form.get('contrasena', '')
    confirmar  = request.form.get('confirmar', '')

    # Documento de identidad OPCIONAL en el registro: si el cliente escribe
    # el número, se valida por tipo y se normaliza; si no, se guarda NULL.
    # El registro NUNCA se bloquea por la API de DNI/RUC. El nombre es
    # editable y marcado "no verificado".
    tipo_doc_nombre = (request.form.get('tipo_documento') or '').strip().lower()
    nro_documento   = normalizar_numero(request.form.get('nro_documento', ''))

    # Validar SOLO si el cliente escribió un número
    if nro_documento:
        if not tipo_doc_nombre:
            tipo_doc_nombre = 'dni'   # default razonable
        doc_ok, doc_msg = validar_documento(tipo_doc_nombre, nro_documento)
        if not doc_ok:
            return render_template('login_cliente.html',
                                   error=doc_msg, registro_activo=True)
    else:
        # Sin número: NULL en la BD (no cadena vacía; no bloquea)
        nro_documento = None
        tipo_doc_nombre = tipo_doc_nombre or 'dni'   # tipo default, número NULL

    if not nombre or not correo or not contrasena:
        return render_template('login_cliente.html',
                               error='Nombre, correo y contrasena son obligatorios.',
                               registro_activo=True)
    if len(direccion) < 5:
        return render_template('login_cliente.html',
                               error='La dirección es obligatoria para registrarte (mínimo 5 caracteres).',
                               registro_activo=True)
    if contrasena != confirmar:
        return render_template('login_cliente.html',
                               error='Las contrasenas no coinciden.',
                               registro_activo=True)
    if len(contrasena) < 6:
        return render_template('login_cliente.html',
                               error='La contrasena debe tener al menos 6 caracteres.',
                               registro_activo=True)

    codigo      = f"{random.randint(0, 999999):06d}"
    hash_pw     = bcrypt.generate_password_hash(contrasena).decode('utf-8')
    codigo_hash = bcrypt.generate_password_hash(codigo).decode('utf-8')

    conn   = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    try:
        # Comparacion insensible a mayusculas y espacios: asi no se crean
        # duplicados ni se bloquea por diferencias de formato.
        cursor.execute("""
            SELECT id_cliente, activo, contrasena
            FROM cliente
            WHERE LOWER(TRIM(email)) = %s
            ORDER BY id_cliente
            LIMIT 1
        """, (correo,))
        existente = cursor.fetchone()

        if existente:
            tiene_clave = bool((existente.get('contrasena') or '').strip())
            cuenta_usable = bool(existente.get('activo')) and tiene_clave

            if cuenta_usable:
                # Bloqueo legitimo: ya hay una cuenta verificada y con clave.
                # Se registra el id en el log para poder ubicarla en la BD.
                app.logger.warning(
                    "Registro bloqueado: el correo %s ya existe como id_cliente=%s "
                    "en la BD Auth (proyecto DATABASE_URI_AUTH).",
                    correo, existente['id_cliente']
                )
                return render_template(
                    'login_cliente.html',
                    error=('Este correo ya tiene una cuenta activa. Inicia sesion o usa '
                           '"Olvide mi contrasena".'),
                    registro_activo=True)

            # Cuenta pendiente de verificar, o creada por el area administrativa
            # sin contrasena: se reutiliza la fila y se vuelve a verificar.
            id_cliente = existente['id_cliente']
            cursor.execute("""
                UPDATE cliente SET nombre=%s, apellido=%s, telefono=%s, contrasena=%s,
                       direccion=%s, id_tipo_documento=%s, nro_documento=%s
                WHERE id_cliente=%s
            """, (nombre, apellido, telefono or None, hash_pw, direccion,
                  _id_tipo_doc, nro_documento or None, id_cliente))
        else:
            # id_tipo_documento=1 (DNI) y nro_documento NULL por defecto;
            # el formulario web no pide documento todavía.
            # OJO: no usar '' — la BD tiene UNIQUE (id_tipo_documento, nro_documento)
            # y un segundo registro con '' violaría la restricción. NULL no choca.
            cursor.execute("""
                INSERT INTO cliente (nombre, apellido, email, telefono, contrasena,
                                     id_tipo_documento, nro_documento, direccion, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                RETURNING id_cliente
            """, (nombre, apellido, correo, telefono or None, hash_pw,
                  _id_tipo_doc, nro_documento or None, direccion))
            id_cliente = cursor.fetchone()['id_cliente']

        # Invalidar codigos anteriores y guardar el nuevo (solo el hash)
        cursor.execute("""
            UPDATE verificacion_cuenta SET usado = TRUE
            WHERE id_cliente = %s AND usado = FALSE
        """, (id_cliente,))
        cursor.execute("""
            INSERT INTO verificacion_cuenta (id_cliente, codigo_hash, creado_en, expira_en, usado)
            VALUES (%s, %s, NOW(), NOW() + INTERVAL '10 minutes', FALSE)
        """, (id_cliente, codigo_hash))
        conn.commit()
    except Exception as e:
        conn.rollback()
        app.logger.exception("Error en registro_cliente")
        return render_template('login_cliente.html',
                               error='No se pudo iniciar el registro. Intenta de nuevo.',
                               registro_activo=True)
    finally:
        conn.close()

    # En sesion solo queda el correo (el codigo vive hasheado en la BD Auth)
    session['reg_correo'] = correo

    resultado = enviar_correo(
        correo,
        'Tu codigo de verificacion CLEOFERR',
        _correo_html_codigo(nombre, codigo, 'Tu codigo de verificacion para crear tu cuenta es:'),
        f"Hola {nombre},\n\nTu codigo de verificacion es: {codigo}\n\nValido por 10 minutos."
    )

    if resultado == 'smtp_no_configurado':
        return render_template('login_cliente.html',
                               error=('El envio de correos no esta configurado en el servidor. '
                                      'Avisa al administrador (falta SMTP_USER/SMTP_PASS).'),
                               registro_activo=True)
    if resultado == 'smtp_auth':
        return render_template('login_cliente.html',
                               verificar_activo=True,
                               correo_verificar=correo,
                               codigo_visible=codigo,
                               aviso_smtp=True,
                               aviso_smtp_motivo=(
                                   'Credenciales SMTP inválidas (revisa SMTP_USER/SMTP_PASS '
                                   'usando una contraseña de aplicación de Gmail).'))
    if resultado == 'smtp_conexion':
        return render_template('login_cliente.html',
                               error=('No se pudo conectar con el servidor de correo. '
                                      'Revisa tu conexion e intenta de nuevo.'),
                               registro_activo=True)
    if resultado:
        return render_template('login_cliente.html',
                               error='No se pudo enviar el correo de verificacion. Intenta de nuevo.',
                               registro_activo=True)

    return render_template('login_cliente.html',
                           verificar_activo=True,
                           correo_verificar=correo)


@app.route('/verificar_registro', methods=['POST'])
def verificar_registro():
    """Verifica el codigo OTP contra verificacion_cuenta y activa la cuenta."""
    from datetime import datetime, timezone

    codigo_ingresado = request.form.get('codigo', '').strip()
    # El correo viene de la sesion; el hidden del formulario es solo respaldo
    # por si la cookie de sesion se perdio.
    correo = session.get('reg_correo') or _normalizar_correo(request.form.get('correo'))

    if not correo:
        return render_template('login_cliente.html',
                               error='Sesion expirada. Por favor registrate de nuevo.',
                               registro_activo=True)

    conn   = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_cliente FROM cliente
            WHERE LOWER(TRIM(email)) = %s
            ORDER BY id_cliente LIMIT 1
        """, (correo,))
        cliente = cursor.fetchone()
        if not cliente:
            return render_template('login_cliente.html',
                                   error='No hay un registro pendiente para este correo.',
                                   registro_activo=True)
        id_cliente = cliente['id_cliente']

        cursor.execute("""
            SELECT id_verificacion, codigo_hash, expira_en,
                   COALESCE(intentos_fallidos, 0) AS intentos_fallidos
            FROM verificacion_cuenta
            WHERE id_cliente = %s AND usado = FALSE
            ORDER BY creado_en DESC LIMIT 1
        """, (id_cliente,))
        verif = cursor.fetchone()

        if not verif:
            return render_template('login_cliente.html',
                                   error='No hay un codigo activo. Solicita uno nuevo desde el registro.',
                                   registro_activo=True)

        # La columna puede ser TIMESTAMP sin zona horaria; comparar un datetime
        # naive con uno aware lanza TypeError. Se normalizan ambos a UTC.
        expira = verif['expira_en']
        if expira.tzinfo is None:
            expira = expira.replace(tzinfo=timezone.utc)
        if expira < datetime.now(timezone.utc):
            return render_template('login_cliente.html',
                                   error='El codigo expiro. Registrate de nuevo para recibir uno nuevo.',
                                   registro_activo=True)

        # Bloqueo por fuerza bruta: 5 intentos como maximo por codigo.
        if verif['intentos_fallidos'] >= 5:
            cursor.execute("UPDATE verificacion_cuenta SET usado = TRUE WHERE id_verificacion = %s",
                           (verif['id_verificacion'],))
            conn.commit()
            return render_template('login_cliente.html',
                                   error='Demasiados intentos fallidos. Solicita un codigo nuevo.',
                                   registro_activo=True)

        try:
            coincide = bcrypt.check_password_hash(verif['codigo_hash'], codigo_ingresado)
        except (ValueError, TypeError):
            coincide = False

        if not coincide:
            cursor.execute("""
                UPDATE verificacion_cuenta
                   SET intentos_fallidos = COALESCE(intentos_fallidos, 0) + 1
                 WHERE id_verificacion = %s
            """, (verif['id_verificacion'],))
            conn.commit()
            return render_template('login_cliente.html',
                                   error='Codigo incorrecto. Intenta de nuevo.',
                                   verificar_activo=True,
                                   correo_verificar=correo)

        cursor.execute("UPDATE verificacion_cuenta SET usado = TRUE WHERE id_verificacion = %s",
                       (verif['id_verificacion'],))
        cursor.execute("UPDATE cliente SET activo = TRUE WHERE id_cliente = %s", (id_cliente,))
        conn.commit()
        session.pop('reg_correo', None)
        return render_template('login_cliente.html',
                               success='Cuenta creada y verificada. Ya puedes iniciar sesion.')
    except Exception as e:
        conn.rollback()
        app.logger.exception(f"Error en verificar_registro ({correo}): {e}")
        return render_template('login_cliente.html',
                               error='Error al verificar el codigo. Intenta de nuevo.',
                               registro_activo=True)
    finally:
        conn.close()


# ── Recuperación de contraseña ──────────────────────────────
def _buscar_cuenta_por_correo(cursor, correo):
    """Busca el correo en usuario y en cliente (BD Auth).
    Devuelve (tipo, dict) donde tipo es 'usuario' o 'cliente', o (None, None)."""
    cursor.execute("SELECT id_usuario, nombres FROM usuario WHERE LOWER(TRIM(email)) = %s", (correo,))
    u = cursor.fetchone()
    if u:
        return 'usuario', u
    cursor.execute("SELECT id_cliente, nombre FROM cliente WHERE LOWER(TRIM(email)) = %s", (correo,))
    c = cursor.fetchone()
    if c:
        return 'cliente', c
    return None, None


@app.route('/recuperar_contrasena', methods=['GET', 'POST'])
def recuperar_contrasena():
    """Paso 1: el usuario ingresa su correo y recibe un código OTP de 6 dígitos."""
    import random

    if request.method == 'GET':
        return render_template('recuperar_contrasena.html')

    correo = _normalizar_correo(request.form.get('correo'))
    if not correo:
        return render_template('recuperar_contrasena.html',
                               error='Ingresa tu correo electrónico.')

    conn   = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    try:
        tipo, cuenta = _buscar_cuenta_por_correo(cursor, correo)
        if not cuenta:
            # Mensaje genérico para no revelar si el correo existe o no
            return render_template('recuperar_contrasena.html',
                                   verificar_activo=True, correo_verificar=correo)

        nombre = cuenta.get('nombres') or cuenta.get('nombre') or correo
        codigo = str(random.randint(100000, 999999))
        codigo_hash = bcrypt.generate_password_hash(codigo).decode('utf-8')

        id_usuario = cuenta['id_usuario'] if tipo == 'usuario' else None
        id_cliente = cuenta['id_cliente'] if tipo == 'cliente' else None

        # Invalidar códigos anteriores de esta cuenta
        cursor.execute("""
            UPDATE recuperacion_contrasena SET usado = TRUE
            WHERE COALESCE(id_usuario, -1) = COALESCE(%s, -1)
              AND COALESCE(id_cliente, -1) = COALESCE(%s, -1)
              AND usado = FALSE
        """, (id_usuario, id_cliente))
        cursor.execute("""
            INSERT INTO recuperacion_contrasena
                (id_usuario, id_cliente, codigo_hash, creado_en, expira_en, usado, ip_solicitud)
            VALUES (%s, %s, %s, NOW(), NOW() + INTERVAL '10 minutes', FALSE, %s)
        """, (id_usuario, id_cliente, codigo_hash, request.remote_addr))
        conn.commit()
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Error en recuperar_contrasena: {e}")
        return render_template('recuperar_contrasena.html',
                               error='No se pudo procesar la solicitud. Intenta de nuevo.')
    finally:
        conn.close()

    resultado = enviar_correo(
        correo,
        f'Tu código para recuperar tu contraseña CLEOFERR: {codigo}',
        _correo_html_codigo(nombre, codigo, 'Tu código para restablecer tu contraseña es:'),
        f"Hola {nombre},\n\nTu código para recuperar tu contraseña es: {codigo}\n\nVálido por 10 minutos."
    )
    if resultado == 'smtp_no_configurado':
        return render_template('recuperar_contrasena.html',
                               error=('El envío de correos no está configurado en el servidor. '
                                      'Avisa al administrador (falta SMTP_USER/SMTP_PASS).'))
    if resultado == 'smtp_auth':
        return render_template('recuperar_contrasena.html',
                               verificar_activo=True, correo_verificar=correo,
                               codigo_visible=codigo, aviso_smtp=True,
                               aviso_smtp_motivo=(
                                   'Credenciales SMTP inválidas (revisa SMTP_USER/SMTP_PASS '
                                   'usando una contraseña de aplicación de Gmail).'))
    if resultado == 'smtp_conexion':
        return render_template('recuperar_contrasena.html',
                               error='No se pudo conectar con el servidor de correo. Intenta de nuevo.')
    if resultado:
        return render_template('recuperar_contrasena.html',
                               error='No se pudo enviar el correo. Intenta de nuevo.')

    return render_template('recuperar_contrasena.html',
                           verificar_activo=True, correo_verificar=correo)


@app.route('/restablecer_contrasena', methods=['POST'])
def restablecer_contrasena():
    """Paso 2: valida el OTP y cambia la contraseña (usuario o cliente)."""
    correo     = _normalizar_correo(request.form.get('correo'))
    codigo     = request.form.get('codigo', '').strip()
    nueva      = request.form.get('nueva', '')
    confirmar  = request.form.get('confirmar', '')

    def _reenviar_con_error(msg):
        return render_template('recuperar_contrasena.html',
                               error=msg, verificar_activo=True,
                               correo_verificar=correo)

    if not correo or not codigo or not nueva:
        return _reenviar_con_error('Todos los campos son obligatorios.')
    if nueva != confirmar:
        return _reenviar_con_error('Las contraseñas no coinciden.')
    if len(nueva) < 6:
        return _reenviar_con_error('La contraseña debe tener al menos 6 caracteres.')

    conn   = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    try:
        tipo, cuenta = _buscar_cuenta_por_correo(cursor, correo)
        if not cuenta:
            return _reenviar_con_error('Solicitud no válida. Vuelve a solicitar el código.')

        id_usuario = cuenta['id_usuario'] if tipo == 'usuario' else None
        id_cliente = cuenta['id_cliente'] if tipo == 'cliente' else None

        cursor.execute("""
            SELECT id_recuperacion, codigo_hash, expira_en, intentos_fallidos
            FROM recuperacion_contrasena
            WHERE COALESCE(id_usuario, -1) = COALESCE(%s, -1)
              AND COALESCE(id_cliente, -1) = COALESCE(%s, -1)
              AND usado = FALSE
            ORDER BY creado_en DESC LIMIT 1
        """, (id_usuario, id_cliente))
        rec = cursor.fetchone()

        if not rec:
            return _reenviar_con_error('No hay un código activo. Solicita uno nuevo.')

        from datetime import datetime, timezone
        if rec['expira_en'] < datetime.now(timezone.utc):
            return _reenviar_con_error('El código expiró. Solicita uno nuevo.')

        if rec['intentos_fallidos'] >= 5:
            cursor.execute("UPDATE recuperacion_contrasena SET usado = TRUE WHERE id_recuperacion = %s",
                           (rec['id_recuperacion'],))
            conn.commit()
            return render_template('recuperar_contrasena.html',
                                   error='Demasiados intentos fallidos. Solicita un nuevo código.')

        try:
            coincide = bcrypt.check_password_hash(rec['codigo_hash'], codigo)
        except (ValueError, TypeError):
            coincide = False

        if not coincide:
            intentos = rec['intentos_fallidos'] + 1
            cursor.execute("""
                UPDATE recuperacion_contrasena
                SET intentos_fallidos = %s, usado = (CASE WHEN %s >= 5 THEN TRUE ELSE usado END)
                WHERE id_recuperacion = %s
            """, (intentos, intentos, rec['id_recuperacion']))
            conn.commit()
            if intentos >= 5:
                return render_template('recuperar_contrasena.html',
                                       error='Demasiados intentos fallidos. Solicita un nuevo código.')
            return _reenviar_con_error(f'Código incorrecto. Te quedan {5 - intentos} intentos.')

        # Código correcto: cambiar contraseña y marcar código como usado
        nuevo_hash = bcrypt.generate_password_hash(nueva).decode('utf-8')
        if tipo == 'usuario':
            cursor.execute("""
                UPDATE usuario SET contrasena = %s, fecha_ultimo_cambio_pwd = NOW()
                WHERE id_usuario = %s
            """, (nuevo_hash, id_usuario))
        else:
            cursor.execute("""
                UPDATE cliente SET contrasena = %s, fecha_ultimo_cambio_pwd = NOW()
                WHERE id_cliente = %s
            """, (nuevo_hash, id_cliente))
        cursor.execute("UPDATE recuperacion_contrasena SET usado = TRUE WHERE id_recuperacion = %s",
                       (rec['id_recuperacion'],))
        conn.commit()

        # Volver al login correspondiente
        if tipo == 'usuario':
            return render_template('login.html',
                                   success='Contraseña actualizada. Inicia sesión con tu nueva clave.')
        return render_template('login_cliente.html',
                               success='Contraseña actualizada. Inicia sesión con tu nueva clave.')
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Error en restablecer_contrasena: {e}")
        return _reenviar_con_error('Error al actualizar la contraseña. Intenta de nuevo.')
    finally:
        conn.close()


@app.route('/mis_pedidos')
def mis_pedidos():
    """Historial de pedidos del cliente con estado actualizado."""
    if not session.get('usuario_id'):
        return redirect(url_for('login_cliente'))
    if session.get('rol') != 'cliente':
        return redirect(url_for('pedidos'))
    conn   = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    pedidos_lista = []
    fases_orden = []
    try:
        # Fases válidas leídas de la BD (misma fuente que el panel admin)
        cursor.execute("SELECT nombre FROM estado_venta WHERE activo = TRUE ORDER BY orden")
        fases_orden = [r['nombre'] for r in cursor.fetchall()]
        fases_orden = [f for f in fases_orden if f != 'cancelado']
        PASOS = fases_orden
        # P4: COUNT del conjunto del CLIENTE (no de la página) y página
        # con ORDER BY estable (id de desempate).
        cursor.execute("SELECT COUNT(*) AS total FROM venta WHERE id_cliente = %s",
                       (session['usuario_id'],))
        total = cursor.fetchone()['total']
        pagina = _pagina_actual(total, POR_PAGINA_CLIENTE)
        cursor.execute(f"""
            SELECT v.id_venta,
                   {SQL_PARED_LIMA.format(p='v.', col='fecha')} AS fecha,
                   ev.nombre AS estado,
                   COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS total
            FROM venta v
            LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
            LEFT JOIN detalle_venta d ON d.id_venta = v.id_venta
            WHERE v.id_cliente = %s
            GROUP BY v.id_venta, v.fecha, ev.nombre
            ORDER BY v.fecha DESC, v.id_venta DESC
            LIMIT %s OFFSET %s
        """, (session['usuario_id'], POR_PAGINA_CLIENTE,
              (pagina - 1) * POR_PAGINA_CLIENTE))
        pedidos_lista = cursor.fetchall()
        for ped in pedidos_lista:
            ped['fecha_peru'] = ped.get('fecha')
            estado = ped.get('estado') or 'pendiente'
            ped['idx_actual'] = PASOS.index(estado) if estado in PASOS else 0
            try:
                cursor.execute(f"""
                    SELECT d.cantidad, d.precio_unitario, d.descuento_unitario,
                           (d.precio_unitario - d.descuento_unitario) AS precio_final,
                           ROUND({SQL_IMPORTE_LINEA.format(p='d.')}, 2) AS importe,
                           p.nombre AS producto_nombre
                    FROM detalle_venta d
                    LEFT JOIN producto p ON d.id_producto = p.id_producto
                    WHERE d.id_venta = %s
                """, (ped['id_venta'],))
                ped['items'] = cursor.fetchall()
            except Exception:
                ped['items'] = []
    except Exception as e:
        app.logger.exception("Error mis_pedidos: %s", e)
    finally:
        conn.close()
    if not fases_orden:
        fases_orden = ['pendiente', 'procesando', 'enviado', 'entregado']
    return render_template('mis_pedidos.html', pedidos=pedidos_lista,
                           fases_orden=fases_orden, pagina=pagina, total=total,
                           por_pagina=POR_PAGINA_CLIENTE,
                           filtros_qs=_filtros_sin_pagina())


@app.route('/pedido_aceptado')
@login_required
def pedido_aceptado():
    """Página de confirmación con fases del pedido para el cliente."""
    if session.get('rol') != 'cliente':
        return redirect(url_for('pedidos'))
    id_pedido = request.args.get('id', '')
    estado    = 'pendiente'
    if id_pedido:
        conn   = get_connection_tienda()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
            SELECT ev.nombre AS estado FROM venta v
            LEFT JOIN estado_venta ev ON v.id_estado_venta = ev.id_estado_venta
            WHERE v.id_venta = %s
        """, (id_pedido,))
            row = cursor.fetchone()
            if row:
                estado = row.get('estado', 'pendiente')
        except Exception:
            app.logger.exception("Error consultando estado del pedido id=%s", id_pedido)
        finally:
            conn.close()
    return render_template('pedido_aceptado.html', id_pedido=id_pedido, estado=estado)


# Transiciones válidas del flujo de pedidos (regla 6): el desplegable y los
# botones se filtran con este mismo mapa. 'entregado' y 'cancelado' son
# finales. Decisión (a) del propietario: pasar a 'confirmado' exige pago
# CONFIRMADO (gate en la ruta); confirmar_pago sigue siendo la única
# puerta al dinero.
TRANSICIONES_PEDIDO = {
    'pendiente': ('confirmado', 'cancelado'),
    'confirmado': ('preparando', 'cancelado'),
    'preparando': ('listo', 'cancelado'),
    'listo': ('entregado',),
    'entregado': (),
    'cancelado': (),
}


@app.route('/pedidos/<int:id>/estado', methods=['POST'])
@login_required
@escritura_required
def pedido_cambiar_estado(id):
    """Vendedor/Admin cambia el estado (fase) de un pedido.

    Contrato (fix del bug del desplegable): si la petición es AJAX, TODAS
    las respuestas son JSON {ok, mensaje, estado_nuevo?} — 400 CSRF (capa
    global), 401/403 sin sesión o permisos (capa global), 404 inexistente,
    409 transición no permitida, 200 éxito o no-op, 500 error interno. Si
    no es AJAX, redirect con flash como hoy. El cambio a 'cancelado'
    respeta B2 (devolución idempotente de stock) y el trigger audita.
    """
    is_ajax = _es_ajax()
    nuevo_estado = (request.form.get('estado') or '').strip()
    conn = get_connection_tienda()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nombre FROM estado_venta WHERE activo = TRUE ORDER BY orden")
        estados_validos = tuple(r[0] for r in cursor.fetchall())

        if nuevo_estado not in estados_validos:
            if is_ajax:
                return {'ok': False, 'mensaje': f'Estado "{nuevo_estado}" no es válido.'}, 400
            flash('Estado no válido.', 'danger')
            return redirect(url_for('pedido_detalle', id=id))

        cursor.execute("""
            SELECT ev.nombre AS actual
              FROM venta v
              JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
             WHERE v.id_venta = %s
        """, (id,))
        fila = cursor.fetchone()
        if not fila:
            if is_ajax:
                return {'ok': False, 'mensaje': 'El pedido no existe.'}, 404
            abort(404)
        actual = fila[0]

        if nuevo_estado == actual:
            # Sin cambios: no es error (idempotente). Hasta aquí solo hubo
            # SELECTs: el finally se encarga de cerrar la transacción.
            if is_ajax:
                return {'ok': True, 'mensaje': 'El pedido ya estaba en ese estado.',
                        'estado_nuevo': actual}
            flash('El pedido ya estaba en ese estado.', 'info')
            return redirect(url_for('pedido_detalle', id=id))

        permitidas = TRANSICIONES_PEDIDO.get(actual, ())
        if nuevo_estado not in permitidas:
            mensaje = ('Confirma el pago antes de preparar el pedido.'
                       if (actual, nuevo_estado) == ('pendiente', 'preparando')
                       else f'Transición no permitida: de "{actual}" a "{nuevo_estado}".')
            if is_ajax:
                return {'ok': False, 'mensaje': mensaje}, 409
            flash(mensaje, 'danger')
            return redirect(url_for('pedido_detalle', id=id))

        if nuevo_estado == 'confirmado':
            # Bloqueo (a): solo se confirma una venta con pago CONFIRMADO.
            # El cambio manual no puede crear ventas "pagadas": confirmar_pago
            # es la única puerta al dinero. POS no usa esta ruta.
            cursor.execute("SELECT estado FROM pago WHERE id_venta = %s", (id,))
            fila_pago = cursor.fetchone()
            if not fila_pago or fila_pago[0] != 'confirmado':
                mensaje = 'El pago no está confirmado; confírmalo desde el flujo de pago.'
                if is_ajax:
                    return {'ok': False, 'mensaje': mensaje}, 409
                flash(mensaje, 'danger')
                return redirect(url_for('pedido_detalle', id=id))

        if nuevo_estado == 'cancelado':
            # B2: solo si la venta NO estaba ya cancelada se devuelve el stock
            # (idempotente: la condición del UPDATE + la marca del movimiento).
            cursor.execute("""
                UPDATE venta
                SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta WHERE nombre = 'cancelado')
                WHERE id_venta = %s
                  AND id_estado_venta <> (SELECT id_estado_venta FROM estado_venta WHERE nombre = 'cancelado')
                RETURNING id_venta
            """, (id,))
            if cursor.fetchone():
                devolver_stock_venta(cursor, id)
        else:
            cursor.execute("""
                UPDATE venta
                SET id_estado_venta = (SELECT id_estado_venta FROM estado_venta WHERE nombre = %s)
                WHERE id_venta = %s
            """, (nuevo_estado, id))
        conn.commit()
        mensaje = f'Estado actualizado a "{nuevo_estado}".'
        if is_ajax:
            return {'ok': True, 'mensaje': mensaje, 'estado_nuevo': nuevo_estado}
        flash(mensaje, 'success')
    except Exception:
        conn.rollback()
        app.logger.exception("Error al actualizar estado del pedido %s", id)
        if is_ajax:
            return {'ok': False, 'mensaje': 'No se pudo actualizar el estado. Intenta de nuevo.'}, 500
        flash('Error al actualizar el estado. Intenta de nuevo.', 'danger')
    finally:
        conn.close()
    # No-AJAX: volver a la lista si el cambio vino desde /pedidos, sino al detalle
    ref = request.referrer or ''
    if '/pedidos?' in ref or ref.rstrip('/').endswith('/pedidos'):
        return redirect(url_for('pedidos'))
    return redirect(url_for('pedido_detalle', id=id))


# ── Helpers del comprobante de venta (simulación boleta/factura) ─────────────

_UNIDADES = ['', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE',
             'OCHO', 'NUEVE', 'DIEZ', 'ONCE', 'DOCE', 'TRECE', 'CATORCE',
             'QUINCE', 'DIECISÉIS', 'DIECISIETE', 'DIECIOCHO', 'DIECINUEVE', 'VEINTE']
_DECENAS  = ['', '', 'VEINTI', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA',
             'SETENTA', 'OCHENTA', 'NOVENTA']
_CENTENAS = ['', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS',
             'QUINIENTOS', 'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS', 'NOVECIENTOS']


def _tres_digitos_a_letras(n):
    """Convierte un número 0-999 a letras en español."""
    if n == 0:
        return ''
    if n == 100:
        return 'CIEN'
    c, resto = divmod(n, 100)
    partes = [_CENTENAS[c]] if c else []
    if resto:
        if resto <= 20:
            partes.append(_UNIDADES[resto])
        else:
            d, u = divmod(resto, 10)
            if d == 2 and u:
                partes.append('VEINTI' + _UNIDADES[u])
            else:
                partes.append(_DECENAS[d] + (' Y ' + _UNIDADES[u] if u else ''))
    return ' '.join(p for p in partes if p)


def numero_a_letras(monto):
    """Devuelve el monto en letras estilo peruano:
    189.90 -> 'CIENTO OCHENTA Y NUEVE CON 90/100 SOLES'."""
    try:
        monto = float(monto or 0)
    except (TypeError, ValueError):
        monto = 0.0
    entero  = int(monto)
    decimos = int(round((monto - entero) * 100))
    if decimos == 100:          # redondeo borde (p. ej. 0.999)
        entero, decimos = entero + 1, 0
    if entero == 0:
        letras = 'CERO'
    else:
        grupos = []
        millones, resto = divmod(entero, 1000000)
        miles, unidades = divmod(resto, 1000)
        if millones:
            txt = _tres_digitos_a_letras(millones)
            grupos.append('UN MILLÓN' if millones == 1 else f'{txt} MILLONES')
        if miles:
            txt = _tres_digitos_a_letras(miles)
            grupos.append('MIL' if miles == 1 else f'{txt} MIL')
        if unidades:
            grupos.append(_tres_digitos_a_letras(unidades))
        letras = ' '.join(grupos)
    return f'{letras} CON {decimos:02d}/100 SOLES'


def _qr_data_uri(texto):
    """Genera un QR PNG (data URI base64) con el texto dado.
    Devuelve None si la librería qrcode no está disponible (la plantilla
    muestra un placeholder en ese caso)."""
    try:
        import io, base64, qrcode
        # Factoría SVG: no requiere Pillow y se imprime nítida a cualquier tamaño.
        from qrcode.image.svg import SvgImage
        img = qrcode.make(texto, image_factory=SvgImage, box_size=6, border=1)
        buf = io.BytesIO()
        img.save(buf)
        return 'data:image/svg+xml;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        app.logger.exception("No se pudo generar el QR del comprobante")
        return None


@app.route('/carrito/confirmar_v2', methods=['POST'])
@login_required
def carrito_confirmar_v2():
    """Confirma el pedido guardando método de pago y generando comprobante."""
    import random, string
    data             = request.get_json() or {}
    items            = data.get('items', [])
    metodo_pago      = data.get('metodo_pago', 'efectivo')
    tipo_comprobante = data.get('tipo_comprobante', 'boleta_simple')
    ruc              = (data.get('ruc') or '').strip() or None
    num_operacion    = (data.get('num_operacion') or '').strip() or None

    if not items:
        return {'ok': False, 'error': 'Carrito vacío'}
    if session.get('rol') != 'cliente':
        return {'ok': False, 'error': 'Solo clientes pueden confirmar pedidos'}

    # Validar el carrito contra la BD: existencia, estado activo, stock y
    # precio oficial. El carrito vive en localStorage (fuente única de verdad
    # en el frontend) y puede traer IDs/precios obsoletos; aquí se corrige.
    # El descuento estimado (oferta vigente) sale del mismo SELECT; el
    # AUTORITATIVO se relee dentro de la TXN que congela el detalle.
    conn_val   = get_connection_tienda()
    cursor_val = conn_val.cursor(dictionary=True)
    ids_items  = {int(it.get('id_producto') or it.get('id') or 0) for it in items}
    cursor_val.execute(f"""
        SELECT p.id_producto, p.nombre, p.precio, p.stock,
               COALESCE(of.descuento_unitario, 0) AS descuento
          FROM producto p
          {SQL_PRECIO_VIGENTE.format(p="p.", a="of")}
         WHERE p.id_producto = ANY(%s) AND p.activo = TRUE
    """, (list(ids_items),))
    prod_db = {r['id_producto']: r for r in cursor_val.fetchall()}
    conn_val.close()

    # Ítems que ya no existen o fueron desactivados: se EXCLUYEN del pedido
    # y se reportan explícitamente al usuario (nunca fallo silencioso).
    excluidos = []
    items_ok  = []
    for it in items:
        pid  = int(it.get('id_producto') or it.get('id') or 0)
        prod = prod_db.get(pid)
        if not prod:
            excluidos.append({'id_producto': pid,
                              'nombre': it.get('nombre') or f'producto #{pid}',
                              'motivo': 'Ya no está disponible (eliminado o desactivado)'})
            continue
        cantidad = max(1, int(it.get('cantidad', 1)))
        stock_db = int(prod['stock'] or 0)
        if stock_db < 1:
            excluidos.append({'id_producto': pid, 'nombre': prod['nombre'],
                              'motivo': 'Sin stock disponible'})
            continue
        if cantidad > stock_db:
            excluidos.append({'id_producto': pid, 'nombre': prod['nombre'],
                              'motivo': f'Stock insuficiente (quedan {stock_db})'})
            continue
        # Precio y nombre oficiales de la BD (ignora los enviados por el
        # cliente); el descuento estimado sirve para el umbral de boleta,
        # el definitivo se congela en la TXN del INSERT.
        items_ok.append({'id_producto': pid,
                         'nombre':      prod['nombre'],
                         'cantidad':    cantidad,
                         'precio':      float(prod['precio']),
                         'descuento':   float(prod['descuento'] or 0)})

    if not items_ok:
        return {'ok': False,
                'error': 'Ningún producto del carrito está disponible.',
                'excluidos': excluidos}

    # Deduplicación: reusar pedido pendiente existente si el mismo cliente
    # tiene un pedido reciente con los mismos productos y misma fecha/hora.
    # Evita crear registros duplicados por doble clic o reenvío.
    cliente_id = session.get('usuario_id')
    conn_check = get_connection_tienda()
    cursor_check = conn_check.cursor(dictionary=True)
    try:
        # Buscar pedidos pendientes del mismo cliente en los últimos 5 minutos
        # (total con la fórmula única: descuento congelado incluido)
        cursor_check.execute(f"""
            SELECT v.id_venta, v.fecha,
                   COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) as total
            FROM venta v
            JOIN detalle_venta d ON v.id_venta = d.id_venta
            WHERE v.id_cliente = %s AND v.id_estado_venta = (SELECT id_estado_venta FROM estado_venta WHERE nombre = 'pendiente')
            AND v.fecha >= NOW() - INTERVAL '5 minutes'
            GROUP BY v.id_venta, v.fecha
            HAVING COUNT(d.id_producto) = (
                SELECT COUNT(*) FROM (VALUES %s) AS tmp(id_producto)
            )
            ORDER BY v.fecha DESC
            LIMIT 1
        """, (cliente_id, tuple(it.get('id_producto') or it.get('id') or 0 for it in items_ok)))
        existing = cursor_check.fetchone()
        if existing:
            order_reuse = {'ok': True, 'id_pedido': existing['id_venta'],
                           'mensaje': 'Pedido existente reutilizado (mismo cliente y productos).',
                           'total': existing['total']}
            conn_check.close()
            return order_reuse
    except Exception:
        app.logger.exception("Error checking for existing order deduplication")
    finally:
        conn_check.close()

    tipo_boleta  = 'electronica' if tipo_comprobante == 'boleta_electronica' else 'simple'
    tipo_comp_db = 'factura' if (ruc and len(ruc) == 11 and ruc.startswith('20')) else 'boleta'
    if tipo_comp_db == 'factura' and not ruc_valido_para_factura(ruc):
        return {'ok': False,
                'error': 'El RUC no es válido (revisa el dígito verificador). El pedido no se creó.'}, 400

    # AUTORRELLENO (documento): el SERVIDOR lee el documento del cliente
    # desde AUTH y no confía en el navegador. Si el cliente ya tiene RUC,
    # el servidor lo usa para factura; si tiene DNI, lo usa para boleta.
    conn_auth_doc = get_connection_auth()
    cursor_auth_doc = conn_auth_doc.cursor(dictionary=True)
    try:
        cursor_auth_doc.execute(
            """SELECT LOWER(TRIM(td.nombre)) AS tipo, c.nro_documento
              FROM cliente c
              LEFT JOIN tipo_documento td ON td.id_tipo_documento = c.id_tipo_documento
             WHERE c.id_cliente = %s""",
            (session['usuario_id'],))
        fila_doc = cursor_auth_doc.fetchone()
    finally:
        conn_auth_doc.close()
    doc_tipo_actual = fila_doc['tipo'] if fila_doc else None
    doc_numero_actual = normalizar_numero(fila_doc['nro_documento']) if fila_doc and fila_doc['nro_documento'] else None

    if doc_tipo_actual == 'ruc' and doc_numero_actual:
        # El servidor TIENE el RUC: lo usa (autoritativo) para factura
        ruc = doc_numero_actual
        tipo_comp_db = 'factura'
        dni = None
    elif doc_tipo_actual == 'ruc' and not doc_numero_actual and tipo_comp_db == 'factura':
        return {'ok': False,
                'error': 'Tu perfil tiene RUC sin número. Corrígelo en tu perfil.'}, 400
    elif doc_numero_actual:
        # El cliente tiene DNI/CE/pasaporte: el servidor lo usa para boleta
        dni = doc_numero_actual
        tipo_comp_db = 'boleta'

    # CLIENTES VARIOS (fase B): boleta sin documento sobre el umbral se
    # rechaza; debajo del umbral se emite a "CLIENTES VARIOS" sin exigir DNI.
    dni = (data.get('dni') or '').strip() or None
    if tipo_comp_db == 'boleta':
        # El umbral de la boleta se evalúa sobre el monto a pagar (con el
        # descuento vigente estimado por el servidor).
        total_estimado = sum(it['cantidad'] * (it['precio'] - it['descuento'])
                             for it in items_ok)
        if boleta_necesita_documento(total_estimado, dni):
            return {'ok': False,
                    'error': f'Para compras sobre S/ {UMBRAL_BOLETA_SIN_DOC:.0f} necesitas '
                             'ingresar tu DNI (o RUC si quieres factura).'}, 400

    metodo_db    = 'yape_plin' if metodo_pago == 'yape' else (
                   'tarjeta'   if metodo_pago == 'tarjeta' else 'efectivo')

    conn   = get_connection_tienda()
    cursor = conn.cursor()
    try:
        # TXN corta (corrección 1): venta + pago 'pendiente' + detalle +
        # stock + movimiento + comprobante (D1). El monto del pago es el
        # total RECALCULADO de detalle_venta (Decimal), jamás del navegador.
        # La pasarela se llama SOLO después del commit: nunca con una
        # transacción abierta ni antes de guardar.
        cursor.execute("""
            INSERT INTO venta (id_tipo_venta, id_cliente, id_estado_venta, num_operacion)
            VALUES ((SELECT id_tipo_venta FROM tipo_venta WHERE nombre = 'online'),
                    %s,
                    (SELECT id_estado_venta FROM estado_venta WHERE nombre = 'pendiente'),
                    %s)
            RETURNING id_venta
        """, (session['usuario_id'], num_operacion))
        id_venta = cursor.fetchone()[0]

        # Si hay caja abierta, asociar el pago a la caja actual
        cursor.execute("SELECT id_caja FROM caja WHERE estado = 'abierta' LIMIT 1")
        fila_caja  = cursor.fetchone()
        id_caja_a  = fila_caja[0] if fila_caja else None

        for it in items_ok:
            id_producto = it['id_producto']
            cantidad    = it['cantidad']
            # Precio y oferta AUTORITATIVOS releídos dentro de la TXN: el
            # descuento (id_oferta + descuento_unitario) se CONGELA aquí
            # y no cambia aunque la oferta venza o se edite después.
            fila_of = oferta_vigente(cursor, id_producto)
            if fila_of is None:
                # El producto desapareció entre la validación y la TXN
                raise ValueError(f"Producto #{id_producto} ya no está disponible.")
            precio_unit = fila_of['precio']        # precio base oficial
            id_oferta   = fila_of['id_oferta']     # None si no hay oferta
            desc_unit   = fila_of['descuento_unitario']
            cursor.execute("""
                INSERT INTO detalle_venta
                    (id_venta, id_producto, cantidad, precio_unitario,
                     id_oferta, descuento_unitario)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (id_venta, id_producto, cantidad, precio_unit,
                  id_oferta, desc_unit))
            cursor.execute("""
                UPDATE producto SET stock = GREATEST(stock - %s, 0) WHERE id_producto = %s
                RETURNING stock
            """, (cantidad, id_producto))
            nuevo_stock = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO inventario_movimiento
                    (tipo, id_producto, id_proveedor, cantidad, precio_unitario,
                     observacion, id_usuario, stock_resultante)
                VALUES ('salida', %s, NULL, %s, %s, %s, NULL, %s)
            """, (id_producto, cantidad, precio_unit,
                  f'Venta online #{id_venta} (cliente #{session["usuario_id"]})',
                  nuevo_stock))

        # Total oficial de la venta, recalculado dentro de la misma TXN con
        # la fórmula única (descuento congelado incluido).
        cursor.execute(f"""
            SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
              FROM detalle_venta WHERE id_venta = %s
        """, (id_venta,))
        total = cursor.fetchone()[0]

        # Pago 'pendiente' con el monto oficial (Decimal) y la pasarela
        cursor.execute("""
            INSERT INTO pago (id_venta, id_tipo_pago, id_caja, estado, pasarela, monto)
            VALUES (%s, (SELECT id_tipo_pago FROM tipo_pago WHERE nombre = %s), %s,
                    'pendiente', %s, %s)
            RETURNING id_pago
        """, (id_venta, metodo_db, id_caja_a, _PASARELA.nombre, total))
        id_pago = cursor.fetchone()[0]

        # D1 (2.2): el comprobante ya NO se crea aquí; se emite al
        # CONFIRMAR el pago (confirmar_pago), de modo que los correlativos
        # solo los consumen ventas pagadas. El tipo y el RUC viajan al
        # detalle_proveedor del pago para esa emisión.

        conn.commit()   # fin de la TXN corta: el pedido quedó guardado

        # AHORA se habla con la pasarela (sin transacción abierta).
        try:
            resultado_pasarela = _PASARELA.crear_pago(PagoSolicitud(
                id_venta=id_venta,
                monto=total,
                metodo=metodo_db,
                clave_idempotencia=f"venta-{id_venta}",
                datos_cliente={'nombre': session.get('nombre') or ''}))
        except Exception:
            app.logger.exception("crear_pago falló para la venta %s", id_venta)
            cancelar_pedido_con_pago_inicial(conn, id_venta)
            return {'ok': False,
                    'error': 'No se pudo iniciar el pago. El pedido fue cancelado. Intenta de nuevo.'}, 502

        if not resultado_pasarela.id_pago_proveedor or resultado_pasarela.estado is not EstadoPago.PENDIENTE:
            cancelar_pedido_con_pago_inicial(conn, id_venta)
            return {'ok': False,
                    'error': 'La pasarela no pudo iniciar el pago. El pedido fue cancelado.'}, 502

        # Segunda TXN: guardar la referencia del proveedor (id_transaccion
        # y metadatos neutrales en detalle_proveedor).
        try:
            cursor.execute("""
                UPDATE pago
                   SET id_transaccion = %s,
                       detalle_proveedor = COALESCE(detalle_proveedor, '{}'::jsonb) || %s::jsonb
                 WHERE id_pago = %s
            """, (resultado_pasarela.id_pago_proveedor,
                  json.dumps({'checkout_url': resultado_pasarela.checkout_url,
                              'requiere_verificacion': bool(resultado_pasarela.requiere_verificacion),
                              'tipo_comprobante': tipo_comp_db,
                              'ruc': ruc,
                              'cliente_documento': dni},
                             ensure_ascii=False),
                  id_pago))
            conn.commit()
        except Exception:
            app.logger.exception("No se pudo guardar la referencia de la pasarela (venta %s)", id_venta)
            cancelar_pedido_con_pago_inicial(conn, id_venta)
            return {'ok': False,
                    'error': 'No se pudo registrar el pago. El pedido fue cancelado. Intenta de nuevo.'}, 500

        session['ultimo_pedido'] = items_ok
        return {'ok': True, 'id_pedido': id_venta, 'id_pago': id_pago,
                'excluidos': excluidos,
                'checkout_url': resultado_pasarela.checkout_url,
                'requiere_verificacion': bool(resultado_pasarela.requiere_verificacion)}, 200

    except Exception:
        conn.rollback()
        app.logger.exception("Error carrito_confirmar_v2")
        return {'ok': False, 'error': 'No se pudo confirmar el pedido. Intenta de nuevo más tarde.'}, 500
    finally:
        conn.close()


# ── Pantalla de pago simulado (checkout de la demo, Tramo 2) ────
# Deshabilitada si PAGO_MODO != 'simulado'. El GET solo consulta (nunca
# escribe). Los datos de tarjeta JAMÁS salen del navegador: el POST recibe
# únicamente el resultado simulado; el monto lo recalcula el servidor desde
# detalle_venta y confirmar_pago sigue siendo la única puerta a 'confirmado'.
def _pago_por_transaccion(cursor, id_transaccion):
    cursor.execute("""
        SELECT p.id_pago, p.id_transaccion, p.monto, p.estado, v.id_venta,
               v.id_cliente, v.id_estado_venta, ev.nombre AS venta_estado
          FROM pago p
          JOIN venta v ON v.id_venta = p.id_venta
          JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
         WHERE p.id_transaccion = %s
    """, (id_transaccion,))
    return cursor.fetchone()


@app.route('/pago/simulado/<id_transaccion>')
@login_required
def pago_simulado(id_transaccion):
    if _PASARELA.modo != 'simulado':
        flash('El pago simulado está deshabilitado (PAGO_MODO).', 'danger')
        return redirect(url_for('catalogo_cliente'))
    conn = get_connection_tienda()
    try:
        cursor = conn.cursor(dictionary=True)
        pago = _pago_por_transaccion(cursor, id_transaccion)
        total = None
        if pago:
            # Total a pagar con la fórmula única (descuento congelado)
            cursor.execute(f"""
                SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
                  FROM detalle_venta WHERE id_venta = %s
            """, (pago['id_venta'],))
            total = cursor.fetchone()['total']
    finally:
        conn.close()
    if not pago:
        abort(404)
    if session.get('rol') == 'cliente' and pago['id_cliente'] != session.get('usuario_id'):
        abort(403)   # un cliente no ve el pago de otro
    if pago['estado'] != 'pendiente':
        flash('Este pago ya fue procesado.', 'info')
        return redirect(url_for('pedido_aceptado', id=pago['id_venta']))
    return render_template('pago_simulado.html', pago=pago, total=total)


@app.route('/pago/simulado/<id_transaccion>/procesar', methods=['POST'])
@login_required
def pago_simulado_procesar(id_transaccion):
    """Recibe SOLO el resultado simulado ('aprobado'|'rechazado') con CSRF.

    Nada de datos de tarjeta: se ignoran campos extra si llegaran. El
    monto se recalcula del servidor y la transición pasa por confirmar_pago.
    """
    if _PASARELA.modo != 'simulado':
        return {'ok': False, 'error': 'pago simulado deshabilitado'}, 403
    data = request.get_json(silent=True) or request.form or {}
    resultado_sim = str(data.get('resultado') or '').strip().lower()
    if resultado_sim not in ('aprobado', 'rechazado'):
        return {'ok': False, 'error': 'resultado simulado inválido'}, 400

    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        pago = _pago_por_transaccion(cursor, id_transaccion)
        if not pago:
            return {'ok': False, 'error': 'pago no encontrado'}, 404
        if session.get('rol') == 'cliente' and pago['id_cliente'] != session.get('usuario_id'):
            return {'ok': False, 'error': 'no autorizado'}, 403
        if pago['estado'] != 'pendiente':
            return {'ok': True, 'estado': 'ya_procesado',
                    'mensaje': f"El pago ya está {pago['estado']}."}, 200

        # Monto del servidor, recalculado de detalle_venta (nunca del
        # navegador) con la fórmula única (descuento congelado)
        cursor.execute(f"""
            SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
              FROM detalle_venta WHERE id_venta = %s
        """, (pago['id_venta'],))
        total = cursor.fetchone()['total']

        estado_final = (EstadoPago.CONFIRMADO if resultado_sim == 'aprobado'
                        else EstadoPago.RECHAZADO)
        id_evento = f"checkout-{id_transaccion}-{resultado_sim}"
        id_pago_evento = registrar_evento(
            cursor, _PASARELA.nombre, id_evento, 'checkout_simulado',
            pago['id_pago'], estado_final.value, True, True,
            {'fuente': 'checkout', 'tipo': 'checkout_simulado'})
        if id_pago_evento is None:
            # doble clic / reenvío del mismo intento: no reprocesar
            conn.commit()
            return {'ok': True, 'estado': 'repetido'}, 200

        res = confirmar_pago(pago['id_pago'],
                             PagoResultado(id_pago_proveedor=id_transaccion,
                                           estado=estado_final, monto=total),
                             conn=conn, fuente='checkout',
                             id_pago_evento=id_pago_evento)
        conn.commit()
        return {'ok': res['estado'] in ('confirmado', 'rechazado',
                                        'ya_confirmado', 'ya_rechazado'),
                'estado': res['estado'], 'motivo': res.get('motivo'),
                'id_pedido': pago['id_venta']}, 200
    except Exception:
        conn.rollback()
        app.logger.exception("Error procesando pago simulado %s", id_transaccion)
        return {'ok': False, 'error': 'error interno'}, 500
    finally:
        conn.close()


# ── Webhook de la pasarela (Fase 2.1, Tramo 2) ──────────────────
# ÚNICA ruta exenta de CSRF: no está en _CSRF_PROTEGIDAS porque la llama
# la pasarela (no el navegador) y se autentica con la firma HMAC del
# secreto PAGO_SIMULADA_WEBHOOK_SECRETO, verificada en tiempo constante
# dentro de la pasarela. Excepción documentada y única.
# crear_pasarela() falla al ARRANCAR si falta el secreto, si es el de
# ejemplo o si PAGO_MODO no es 'simulado' (validación en registro.py).
_PASARELA = crear_pasarela()


def _procesar_evento_pago(ev, id_evento_fallback=None):
    """Núcleo del webhook: reclamar el evento (idempotencia por id),
    registrar rechazados sin tocar el pago y pasar por confirmar_pago.
    Lo usan la ruta HTTP y el gatillo del panel de demo. Devuelve
    (respuesta, codigo_http)."""
    id_evento = ev.id_evento or id_evento_fallback or ('int-' + uuid.uuid4().hex[:16])
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        id_pago = None
        if ev.id_pago_proveedor:
            cursor.execute(
                "SELECT id_pago FROM pago WHERE id_transaccion = %s LIMIT 1",
                (ev.id_pago_proveedor,))
            fila = cursor.fetchone()
            id_pago = fila['id_pago'] if fila else None

        detalle = {'tipo': str(ev.detalle_sin_sensibles.get('tipo') or ''),
                   'fuente': 'webhook'}
        id_pago_evento = registrar_evento(
            cursor, _PASARELA.nombre, id_evento, detalle['tipo'] or 'evento',
            id_pago, ev.estado.value if ev.estado else None,
            ev.firma_valida, not ev.fuera_de_tiempo, detalle)

        if id_pago_evento is None:
            # Evento ya registrado: solo se reanuda si antes fue rechazado
            # por firma o quedó sin procesar (p. ej. bloqueado); si ya se
            # procesó, es un reenvío y no se reprocesa nada.
            cursor.execute("""SELECT id_pago_evento, firma_valida, procesado_en
                                FROM pago_evento
                               WHERE proveedor = %s AND id_evento_proveedor = %s""",
                           (_PASARELA.nombre, id_evento))
            previo = cursor.fetchone()
            reanudar = (ev.firma_valida and not ev.fuera_de_tiempo
                        and previo
                        and (not previo['firma_valida'] or previo['procesado_en'] is None))
            if reanudar:
                cursor.execute("""UPDATE pago_evento
                                     SET firma_valida = TRUE,
                                         dentro_de_tolerancia = %s,
                                         estado_resultante = %s,
                                         id_pago = %s,
                                         detalle = %s,
                                         procesado_en = NULL
                                   WHERE id_pago_evento = %s""",
                               (not ev.fuera_de_tiempo,
                                ev.estado.value if ev.estado else None,
                                id_pago, json.dumps(detalle, ensure_ascii=False),
                                previo['id_pago_evento']))
                id_pago_evento = previo['id_pago_evento']
            else:
                conn.commit()
                return {'ok': True, 'estado': 'repetido'}, 200

        if not ev.firma_valida:
            conn.commit()
            return {'ok': False, 'error': 'firma inválida'}, 400
        if ev.fuera_de_tiempo:
            conn.commit()
            return {'ok': False, 'error': 'evento fuera de tolerancia'}, 400
        if ev.estado is None or ev.estado is EstadoPago.DESCONOCIDO:
            # 'desconocido' JAMAS se escribe en pago.estado (regla D7).
            conn.commit()
            return {'ok': True, 'estado': 'ignorado'}, 200
        if id_pago is None:
            conn.commit()
            return {'ok': True, 'estado': 'sin_pago_asociado'}, 200

        resultado = PagoResultado(id_pago_proveedor=ev.id_pago_proveedor,
                                  estado=ev.estado, monto=ev.monto)
        res = confirmar_pago(id_pago, resultado, conn=conn, fuente='webhook',
                             id_pago_evento=id_pago_evento)
        conn.commit()
        codigo = 200
        if res['estado'] == 'bloqueado':
            # El proveedor debe reintentar el mismo evento: quedó sin
            # procesar (procesado_en NULL) y la reanudación lo retomará.
            codigo = 503
        return {'ok': res['estado'] in ('confirmado', 'ya_confirmado',
                                       'rechazado', 'ya_rechazado'),
                'estado': res['estado'], 'motivo': res.get('motivo')}, codigo
    except Exception:
        conn.rollback()
        app.logger.exception("Error procesando webhook de pago")
        return {'ok': False, 'error': 'error interno'}, 500
    finally:
        conn.close()


@app.route('/webhooks/pago/<proveedor>', methods=['POST'])
def webhook_pago(proveedor):
    """Notificaciones del proveedor configurado (nunca de nombres de URL).

    Flujo en UNA transacción por petición: verificar firma -> reclamar el
    evento (idempotencia por id de evento en pago_evento) -> registrar
    rechazados sin tocar el pago -> confirmar_pago (única puerta a
    'confirmado'). Solo POST: nunca se escribe en un GET.
    """
    if proveedor != _PASARELA.nombre:
        abort(404)
    payload = request.get_data()
    ev = _PASARELA.procesar_webhook(payload, request.headers)
    id_evento = ev.id_evento or ('sin-id-' + hashlib.sha256(payload).hexdigest()[:24])
    return _procesar_evento_pago(ev, id_evento)


# ── Panel de demo: eventos de pago simulado (2c) ────────────────
# Solo administradores. El gatillo dispara un webhook simulado FIRMADO por
# el servidor (el secreto jamás sale de él). El GET solo consulta: nunca
# se escribe en un GET. Deshabilitado si PAGO_MODO != 'simulado'.
@app.route('/admin/pagos_simulados')
@login_required
@admin_required
def admin_pagos_simulados():
    if _PASARELA.modo != 'simulado':
        flash('Panel de pagos simulados deshabilitado (PAGO_MODO).', 'danger')
        return redirect(url_for('productos'))
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT COUNT(*) AS total FROM pago_evento")
        total = cursor.fetchone()['total']
        pagina = _pagina_actual(total, POR_PAGINA_ADMIN)
        cursor.execute(f"""
            SELECT {SQL_PARED_LIMA.format(p='pe.', col='recibido_en')} AS recibido_en,
                   pe.proveedor, pe.id_evento_proveedor, pe.tipo,
                   pe.id_pago, pe.estado_resultante, pe.firma_valida,
                   pe.dentro_de_tolerancia, pe.procesado_en, pe.detalle,
                   p.id_transaccion, p.monto AS pago_monto, v.id_venta
              FROM pago_evento pe
              LEFT JOIN pago p ON p.id_pago = pe.id_pago
              LEFT JOIN venta v ON v.id_venta = p.id_venta
             ORDER BY pe.recibido_en DESC, pe.id_pago_evento DESC
             LIMIT %s OFFSET %s
        """, (POR_PAGINA_ADMIN, (pagina - 1) * POR_PAGINA_ADMIN))
        eventos = cursor.fetchall()
        cursor.execute("""
            SELECT p.id_pago, p.id_transaccion, p.monto, v.id_venta
              FROM pago p
              JOIN venta v ON v.id_venta = p.id_venta
             WHERE p.estado = 'pendiente' AND p.id_transaccion IS NOT NULL
               AND p.monto IS NOT NULL
             ORDER BY v.id_venta DESC
             LIMIT 50
        """)
        pendientes = cursor.fetchall()
    finally:
        conn.close()
    return render_template('admin_pagos_simulados.html',
                           eventos=eventos, pendientes=pendientes,
                           pagina=pagina, total=total,
                           por_pagina=POR_PAGINA_ADMIN,
                           filtros_qs=_filtros_sin_pagina())


@app.route('/admin/pagos_simulados/disparar', methods=['POST'])
@login_required
@admin_required
def admin_pagos_disparar():
    """Gatillo de la demo: dispara un evento firmado para un pago
    PENDIENTE. El monto lo recalcula el servidor desde detalle_venta."""
    if _PASARELA.modo != 'simulado':
        return {'ok': False, 'error': 'deshabilitado'}, 403
    id_pago = request.form.get('id_pago', type=int)
    estado = (request.form.get('estado') or '').strip()
    if not id_pago or estado not in ('confirmado', 'rechazado'):
        flash('Parámetros inválidos para el gatillo de demo.', 'danger')
        return redirect(url_for('admin_pagos_simulados'))
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT p.id_pago, p.id_transaccion, p.id_venta
              FROM pago p
             WHERE p.id_pago = %s AND p.estado = 'pendiente'
               AND p.id_transaccion IS NOT NULL
        """, (id_pago,))
        pago = cursor.fetchone()
        if not pago:
            flash('El pago no existe o ya no está pendiente.', 'warning')
            return redirect(url_for('admin_pagos_simulados'))
        # Total con la fórmula única para firmar el evento del gatillo demo
        cursor.execute(f"""
            SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='')}), 0) AS total
              FROM detalle_venta WHERE id_venta = %s
        """, (pago['id_venta'],))
        total = cursor.fetchone()['total']
    finally:
        conn.close()
    payload, headers = _PASARELA.firmar_evento(
        pago['id_transaccion'], EstadoPago(estado), total, tipo='pago_actualizado')
    ev = _PASARELA.procesar_webhook(payload, headers)
    respuesta, _codigo = _procesar_evento_pago(ev)
    est = respuesta.get('estado')
    if respuesta.get('ok') or est in ('repetido', 'ignorado', 'sin_pago_asociado'):
        flash(f"Evento simulado procesado: estado '{est}'.", 'success')
    else:
        flash(f"Evento simulado no aplicado: {respuesta.get('error') or respuesta.get('motivo') or est}.", 'warning')
    return redirect(url_for('admin_pagos_simulados'))


# -- Perfil del cliente (documento de identidad) ----------------
@app.route('/perfil')
@login_required
def perfil():
    """Perfil del cliente: ver y corregir su documento de identidad.

    GET solo lectura; POST actualiza el documento con validación.
    Aviso de snapshot: los comprobantes ya emitidos NO cambian.
    """
    if session.get('rol') != 'cliente':
        return redirect(url_for('pedidos'))
    conn = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT c.nombre, c.apellido, c.email, c.direccion, c.telefono,
                   LOWER(TRIM(td.nombre)) AS tipo_doc, c.nro_documento
              FROM cliente c
              LEFT JOIN tipo_documento td ON td.id_tipo_documento = c.id_tipo_documento
             WHERE c.id_cliente = %s
        """, (session['usuario_id'],))
        fila = cursor.fetchone()
        if not fila:
            flash('No se pudo cargar tu perfil.', 'danger')
            return redirect(url_for('mis_pedidos'))
        doc_actual = {
            'tipo': fila['tipo_doc'] or '',
            'numero': normalizar_numero(fila['nro_documento']) if fila['nro_documento'] else None,
        }
        cliente = {
            'nombre': fila['nombre'], 'apellido': fila['apellido'],
            'email': fila['email'], 'direccion': fila['direccion'],
            'telefono': fila['telefono'],
        }
    finally:
        conn.close()
    from services.comprobantes import UMBRAL_BOLETA_SIN_DOC
    return render_template('perfil.html', cliente=cliente, doc_actual=doc_actual,
                           umbral_sin_doc=int(UMBRAL_BOLETA_SIN_DOC))


@app.route('/perfil', methods=['POST'])
@login_required
def perfil_actualizar_documento():
    """Actualiza el documento de identidad del cliente (validado + normalizado)."""
    if session.get('rol') != 'cliente':
        return redirect(url_for('pedidos'))
    tipo_doc_nombre = (request.form.get('tipo_documento') or '').strip().lower()
    nro_documento   = normalizar_numero(request.form.get('nro_documento', ''))
    ok, msg = validar_documento(tipo_doc_nombre, nro_documento)
    if not ok:
        flash(msg, 'danger')
        return redirect(url_for('perfil'))
    conn = get_connection_auth()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_tipo_documento FROM tipo_documento
             WHERE LOWER(TRIM(nombre)) = %s
        """, (tipo_doc_nombre,))
        fila_td = cursor.fetchone()
        if not fila_td:
            flash('Tipo de documento no encontrado.', 'danger')
            return redirect(url_for('perfil'))
        id_tipo_doc = fila_td['id_tipo_documento']
        try:
            cursor.execute("""
                UPDATE cliente SET id_tipo_documento=%s, nro_documento=%s
                 WHERE id_cliente=%s
            """, (id_tipo_doc, nro_documento, session['usuario_id']))
            conn.commit()
            flash('Documento actualizado correctamente.', 'success')
        except Exception as e:
            conn.rollback()
            if 'uq_cliente_documento' in str(e):
                flash('Ese documento ya está registrado por otro cliente.', 'danger')
            else:
                app.logger.exception("Error actualizando documento del cliente")
                flash('No se pudo actualizar el documento.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('perfil'))


# ── Reservas de stock vencidas (Fase C) ─────────────────────────
# Botón POST admin con vista previa en el GET (solo lectura).
# Plazos: 30 min tarjeta / 24 h N.o de operación, desde venta.fecha,
# SOLO mientras el pago siga 'pendiente'. Nunca en un GET.
@app.route('/admin/reservas')
@login_required
@admin_required
def admin_reservas():
    """Vista previa de reservas vencidas: qué se libera si pulsas el botón.

    Solo lectura: NO libera nada. Muestra ids, tipo de pago y tiempo
    transcurrido (sin datos personales).
    """
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        # P3: la tabla se pagina (20 por página), pero el número que
        # muestran el aviso y el botón es el TOTAL del conjunto completo.
        total = total_reservas_vencidas(cursor)
        pagina = _pagina_actual(total, POR_PAGINA_ADMIN)
        vencidas = preview_reservas_vencidas(
            cursor, POR_PAGINA_ADMIN, (pagina - 1) * POR_PAGINA_ADMIN)
    finally:
        conn.close()
    return render_template('admin_reservas.html', vencidas=vencidas,
                           total_vencidas=total, pagina=pagina,
                           por_pagina=POR_PAGINA_ADMIN,
                           filtros_qs=_filtros_sin_pagina())


@app.route('/admin/reservas/liberar', methods=['POST'])
@login_required
@admin_required
def admin_reservas_liberar():
    """Libera TODAS las reservas vencidas (venta+pago->cancelado, stock B2).

    Transacción única con FOR UPDATE por venta; idempotente.
    El trigger de auditoria registra cada cancelación.
    """
    conn = get_connection_tienda()
    try:
        resultado = liberar_reservas_vencidas(conn)
    except Exception:
        conn.rollback()
        app.logger.exception("Error liberando reservas vencidas")
        flash('Error al liberar las reservas. Revisa el log.', 'danger')
        return redirect(url_for('admin_reservas'))
    finally:
        conn.close()
    if resultado['liberadas'] > 0:
        flash(f"Se liberaron {resultado['liberadas']} reservas "
              f"(ids: {', '.join(str(i) for i in resultado['ids'])}).",
              'success')
    else:
        flash('No había reservas vencidas que liberar.', 'info')
    if resultado['errores'] > 0:
        flash(f"{resultado['errores']} ventas no se pudieron liberar "
              '(revisa el log).', 'warning')
    return redirect(url_for('admin_reservas'))


# ── Consultas RUC/DNI (proxy seguro hacia la API Perú; placeholder de la futura API SUNAT) ──
# El token NUNCA se expone al cliente: se lee únicamente del entorno del servidor.
# -- Dashboard del administrador (Fase D: KPIs y menu) ------------
# Solo lectura: NUNCA escribe en un GET. Ruta admin-only.
@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    """Panel de administracion: 6 KPIs + 2 graficos (solo lectura).

    Criterio global: solo pagos 'confirmado' cuentan como vendidos.
    Zona horaria: America/Lima para todos los agrupamientos por dia.
    """
    conn = get_connection_tienda()
    cursor = conn.cursor(dictionary=True)
    try:
        kpis = {}
        cursor.execute(f"""
            SELECT COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='d.')}), 0) AS ventas_hoy
              FROM venta v
              JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
              JOIN detalle_venta d ON d.id_venta = v.id_venta
             WHERE {SQL_DIA_LIMA.format(p='v.', col='fecha')} = {SQL_HOY_LIMA}
        """)
        kpis['ventas_hoy'] = float(cursor.fetchone()['ventas_hoy'] or 0)

        cursor.execute("""
            SELECT COUNT(*) AS pendientes_pago
              FROM venta v
              JOIN pago p ON p.id_venta = v.id_venta AND p.estado = 'pendiente'
              JOIN tipo_venta tv ON tv.id_tipo_venta = v.id_tipo_venta
              JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
             WHERE ev.nombre = 'pendiente' AND tv.nombre <> 'local'
        """)
        kpis['pendientes_pago'] = int(cursor.fetchone()['pendientes_pago'])

        cursor.execute("""
            SELECT COUNT(*) AS por_preparar
              FROM venta v
              JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
             WHERE ev.nombre = 'confirmado'
        """)
        kpis['por_preparar'] = int(cursor.fetchone()['por_preparar'])

        cursor.execute("""
            SELECT COUNT(*) AS stock_bajo
              FROM producto
             WHERE activo = TRUE AND stock < COALESCE(stock_minimo, 15)
        """)
        kpis['stock_bajo'] = int(cursor.fetchone()['stock_bajo'])

        cursor.execute(f"""
            SELECT COALESCE(
                       SUM({SQL_IMPORTE_LINEA.format(p='d.')}) / NULLIF(COUNT(DISTINCT v.id_venta), 0),
                       0) AS ticket_promedio
              FROM venta v
              JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
              JOIN detalle_venta d ON d.id_venta = v.id_venta
             WHERE v.fecha >= NOW() - INTERVAL '30 days'
        """)
        kpis['ticket_promedio'] = round(float(cursor.fetchone()['ticket_promedio'] or 0), 2)

        cursor.execute(f"""
            SELECT COUNT(*) FILTER (WHERE estado_sunat = 'enviado') AS emitidos,
                   COUNT(*) FILTER (WHERE estado_sunat = 'pendiente') AS pendientes
              FROM comprobante
             WHERE {SQL_DIA_LIMA.format(p='', col='fecha_emision')} = {SQL_HOY_LIMA}
        """)
        fila_comp = cursor.fetchone()
        kpis['comprobantes_emitidos'] = int(fila_comp['emitidos'])
        kpis['comprobantes_pendientes'] = int(fila_comp['pendientes'])

        cursor.execute(f"""
            WITH dias AS (
                SELECT (d::date) AS dia
                  FROM generate_series(
                      {SQL_HOY_LIMA} - 6,
                      {SQL_HOY_LIMA},
                      '1 day'::interval
                  ) d
            ),
            ventas_dia AS (
                SELECT {SQL_DIA_LIMA.format(p='v.', col='fecha')} AS dia,
                       COALESCE(SUM({SQL_IMPORTE_LINEA.format(p='det.')}), 0) AS total
                  FROM venta v
                  JOIN pago pg ON pg.id_venta = v.id_venta AND pg.estado = 'confirmado'
                  JOIN detalle_venta det ON det.id_venta = v.id_venta
                 WHERE v.fecha >= NOW() - INTERVAL '7 days'
                 GROUP BY 1
            )
            SELECT TO_CHAR(d.dia, 'DD/MM') AS etiqueta,
                   COALESCE(v.total, 0) AS total
              FROM dias d
              LEFT JOIN ventas_dia v ON v.dia = d.dia
             ORDER BY d.dia
        """)
        ventas_por_dia = cursor.fetchall()

        cursor.execute("""
            SELECT ev.nombre AS estado, COUNT(*) AS n
              FROM venta v
              JOIN estado_venta ev ON ev.id_estado_venta = v.id_estado_venta
             GROUP BY ev.nombre, ev.orden
             ORDER BY ev.orden
        """)
        pedidos_por_estado = cursor.fetchall()

        from services.reservas import preview_reservas_vencidas
        vencidas = preview_reservas_vencidas(cursor)
        reservas_vencidas = len(vencidas)
    finally:
        conn.close()

    return render_template('admin_dashboard.html',
                           kpis=kpis,
                           ventas_por_dia=ventas_por_dia,
                           pedidos_por_estado=pedidos_por_estado,
                           reservas_vencidas=reservas_vencidas)


import json as _json
import urllib.request as _urllib_request
import urllib.error as _urllib_error

SUNAT_API_BASE  = os.environ.get("BASE_SUNAT", "https://dniruc.apisperu.com/api/v1")
SUNAT_API_TOKEN = os.environ.get("TOKEN_SUNAT")  # sin valor por defecto: jamás hardcodeado

def _consultar_api_sunat(tipo, numero):
    """Consulta la API Perú (simulación de SUNAT) desde el servidor y devuelve (data, status, error)."""
    if not SUNAT_API_TOKEN:
        return None, 503, "Servicio no configurado: falta TOKEN_SUNAT en el entorno del servidor."
    url = f"{SUNAT_API_BASE}/{tipo}/{numero}?token={SUNAT_API_TOKEN}"
    req = _urllib_request.Request(url, headers={"Accept": "application/json"})
    try:
        with _urllib_request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            return data, 200, None
    except _urllib_error.HTTPError as e:
        try:
            data = _json.loads(e.read().decode("utf-8"))
        except Exception:
            data = None
        return data, e.code, f"Error de la API externa ({e.code})"
    except Exception as e:
        app.logger.exception("Error consultando API RUC/DNI: %s", e)
        return None, 502, "No se pudo conectar al servicio de consulta."

@app.route('/api/consultar_ruc/<ruc>')
def api_consultar_ruc(ruc):
    if not (ruc.isdigit() and len(ruc) == 11):
        return {'ok': False, 'error': 'RUC inválido: debe tener 11 dígitos.'}, 400
    data, status, err = _consultar_api_sunat('ruc', ruc)
    if err and data is None:
        return {'ok': False, 'error': err}, status
    resp = dict(data) if isinstance(data, dict) else {}
    resp['ok'] = bool(resp.get('razonSocial') or resp.get('razon_social') or resp.get('nombre'))
    return resp, 200 if resp['ok'] else status

@app.route('/api/consultar_dni/<dni>')
def api_consultar_dni(dni):
    if not (dni.isdigit() and len(dni) == 8):
        return {'ok': False, 'error': 'DNI inválido: debe tener 8 dígitos.'}, 400
    data, status, err = _consultar_api_sunat('dni', dni)
    if err and data is None:
        return {'ok': False, 'error': err}, status
    resp = dict(data) if isinstance(data, dict) else {}
    resp['ok'] = bool(resp.get('nombres') or resp.get('nombre') or resp.get('apellidoPaterno') or resp.get('apellido_paterno'))
    return resp, 200 if resp['ok'] else status


if __name__ == '__main__':
    # debug solo si FLASK_DEBUG=1 explícitamente en el entorno (nunca en producción)
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")