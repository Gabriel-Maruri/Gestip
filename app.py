"""Gestip — Gestión Personal Integral (Finanzas, Nutrición y Entrenamiento).

Módulo principal de la aplicación web Flask con PostgreSQL y arquitectura Clean Code.
"""

import calendar
import csv
import io
import json
import logging
import os
import secrets
from datetime import date, timedelta
from functools import wraps
from urllib.parse import urlparse

import psycopg2
from flask import (
    Flask,
    Response,
    g,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.pool import ThreadedConnectionPool
from werkzeug.security import check_password_hash, generate_password_hash

# -----------------------------------------------------------------------------
# Configuración de Logging y Entorno
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("gestip")

# Carga de variables de entorno locales (.env ignorado en git)
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    val = val.strip().strip("'\"")
                    os.environ.setdefault(key.strip(), val)
    except Exception as env_err:
        logger.warning("No se pudo leer el archivo .env: %s", env_err)

app = Flask(__name__)

# Protocolos de seguridad: Clave secreta persistente y configuración de cookies
secret_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
if not os.path.exists(secret_file):
    with open(secret_file, "w", encoding="utf-8") as sf:
        sf.write(secrets.token_hex(32))

with open(secret_file, "r", encoding="utf-8") as sf:
    app.secret_key = os.getenv("SECRET_KEY", sf.read().strip())

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Conexión a base de datos PostgreSQL
DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/gestion")

MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DIAS_SEMANA_COMPLETO = [
    "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"
]
MOMENTOS_DIA = ["Desayuno", "Almuerzo", "Cena", "Snack"]


# -----------------------------------------------------------------------------
# Gestión de Pool de Conexiones PostgreSQL (ThreadedConnectionPool)
# -----------------------------------------------------------------------------
_db_pool = None


def get_pool():
    """Obtiene o inicializa el pool de conexiones de base de datos."""
    global _db_pool
    if _db_pool is None or _db_pool.closed:
        _db_pool = ThreadedConnectionPool(minconn=2, maxconn=10, dsn=DSN)
        logger.info("Pool de conexiones PostgreSQL inicializado correctamente.")
    return _db_pool


def get_db():
    """Obtiene una conexión activa desde el pool para la solicitud actual."""
    if not hasattr(g, "db") or g.db is None or g.db.closed:
        pool = get_pool()
        g.db = pool.getconn()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    """Devuelve la conexión de base de datos al pool al finalizar la petición."""
    db = g.pop("db", None)
    if db is not None:
        try:
            if not db.closed:
                db.rollback()
            get_pool().putconn(db)
        except Exception as exc:
            logger.warning("Error devolviendo conexión al pool: %s", exc)


def q(sql, args=(), one=False):
    """Ejecuta una consulta SQL en una conexión del pool y retorna diccionarios."""
    if has_request_context():
        con = get_db()
        with con:
            with con.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, args)
                if cur.description:
                    rows = cur.fetchall()
                    return (rows[0] if rows else None) if one else rows
                return None
    else:
        pool = get_pool()
        con = pool.getconn()
        try:
            with con:
                with con.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, args)
                    if cur.description:
                        rows = cur.fetchall()
                        return (rows[0] if rows else None) if one else rows
                    return None
        finally:
            pool.putconn(con)


# -----------------------------------------------------------------------------
# Funciones Utilitarias y Validadores de Seguridad
# -----------------------------------------------------------------------------
def is_safe_url(target):
    """Valida que la URL de redirección sea local y segura (previene Open Redirect)."""
    if not target or target.startswith("//") or target.startswith("\\"):
        return False
    parsed = urlparse(target)
    if parsed.netloc:
        return False
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return False
    return target.startswith("/")


def parse_float(val, default=0.0, min_val=None, max_val=None):
    """Parsea un número flotante de forma segura con límites opcionales."""
    try:
        num = float(val)
        if min_val is not None and num < min_val:
            return default
        if max_val is not None and num > max_val:
            return default
        return num
    except (ValueError, TypeError):
        return default


def parse_int(val, default=0, min_val=None, max_val=None):
    """Parsea un entero de forma segura con límites opcionales."""
    try:
        num = int(val)
        if min_val is not None and num < min_val:
            return default
        if max_val is not None and num > max_val:
            return default
        return num
    except (ValueError, TypeError):
        return default


def parse_date_str(val, default=None):
    """Parsea una fecha ISO YYYY-MM-DD o retorna el valor predeterminado."""
    if not val:
        return default or date.today()
    try:
        return date.fromisoformat(str(val).strip())
    except (ValueError, TypeError):
        return default or date.today()


def get_month_range(year, month):
    """Retorna (inicio_mes, inicio_siguiente_mes) como fechas sargables."""
    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month + 1, 1)
    return start_date, end_date


# -----------------------------------------------------------------------------
# Control de Acceso y Manejadores de Errores
# -----------------------------------------------------------------------------
@app.before_request
def cargar_usuario():
    """Carga el usuario activo desde la sesión de forma limpia sin DDL en caliente."""
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        usuario = q(
            "SELECT id, nombre, email FROM usuario WHERE id = %s",
            (user_id,),
            one=True
        )
        if usuario:
            g.user = usuario
        else:
            session.clear()


def login_required(func):
    """Decorador para proteger rutas que requieren autenticación."""
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return func(*args, **kwargs)
    return decorated_function


@app.errorhandler(404)
def not_found_error(error):
    """Manejo centralizado de errores 404 para vistas HTML y llamadas AJAX."""
    if request.path.startswith("/alimentacion/buscar") or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": False, "error": "Recurso no encontrado"}), 404
    return render_template("login.html", err="La página solicitada no existe."), 404


@app.errorhandler(500)
def internal_error(error):
    """Manejo centralizado de excepciones 500 inesperadas."""
    logger.error("Error interno del servidor no controlado: %s", error)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500
    return render_template("login.html", err="Ocurrió un error inesperado en el servidor."), 500


# -----------------------------------------------------------------------------
# Rutas de Autenticación y Landing
# -----------------------------------------------------------------------------
@app.route("/")
def inicio():
    """Ruta raíz de bienvenida: redirige al dashboard si autenticado, o a login."""
    if g.user:
        return redirect(url_for("general"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Autenticación de usuarios existentes con saneamiento de redirección."""
    if g.user:
        return redirect(url_for("general"))

    err = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        usuario = q(
            "SELECT id, nombre, email, password_hash FROM usuario WHERE lower(email) = %s",
            (email,),
            one=True
        )
        if usuario and check_password_hash(usuario["password_hash"], password):
            session.clear()
            session["user_id"] = usuario["id"]
            session.permanent = (request.form.get("recordar") == "1")

            next_url = request.args.get("next") or request.form.get("next")
            if not next_url or not is_safe_url(next_url):
                next_url = url_for("general")
            return redirect(next_url)
        else:
            err = "Credenciales incorrectas. Verifica tu correo y contraseña."

    return render_template("login.html", err=err)


@app.route("/registro", methods=["GET", "POST"])
def registro():
    """Registro de nuevos usuarios con creación en lote de categorías y perfil."""
    if g.user:
        return redirect(url_for("general"))

    err = None
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not nombre or not email or len(password) < 6:
            err = "Por favor completa todos los campos (la contraseña debe tener al menos 6 caracteres)."
        else:
            existente = q(
                "SELECT id FROM usuario WHERE lower(email) = %s",
                (email,),
                one=True
            )
            if existente:
                err = "Ya existe una cuenta registrada con ese correo electrónico."
            else:
                pwd_hash = generate_password_hash(password)
                nuevo_id = q("""
                    INSERT INTO usuario (nombre, email, password_hash)
                    VALUES (%s, %s, %s) RETURNING id
                """, (nombre, email, pwd_hash), one=True)["id"]

                # Inicialización vectorizada de categorías de egreso base
                cats_base = [
                    (nuevo_id, "Alimentación y Super", 300),
                    (nuevo_id, "Vivienda y Alquiler", 500),
                    (nuevo_id, "Transporte", 100),
                    (nuevo_id, "Servicios y Facturas", 120),
                    (nuevo_id, "Salud y Farmacia", 60),
                    (nuevo_id, "Educación", 80),
                    (nuevo_id, "Ocio y Entretenimiento", 100),
                    (nuevo_id, "Ropa y Calzado", 50),
                    (nuevo_id, "Otros Gastos", 50),
                ]
                con = get_db()
                with con:
                    with con.cursor() as cur:
                        execute_values(
                            cur,
                            "INSERT INTO categoria_egreso (user_id, nombre, presupuesto_mensual) VALUES %s",
                            cats_base
                        )

                # Inicializar metas nutricionales predeterminadas
                q("""
                    INSERT INTO perfil_nutricional (user_id, meta_kcal, meta_proteina, meta_carbos, meta_grasa, meta_agua_ml)
                    VALUES (%s, 2000, 140, 220, 60, 2000)
                """, (nuevo_id,))

                session.clear()
                session["user_id"] = nuevo_id
                session.permanent = True
                return redirect(url_for("general"))

    return render_template("registro.html", err=err)


@app.route("/logout")
def logout():
    """Cierra la sesión del usuario actual."""
    session.clear()
    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# Módulo General (Dashboard Integral)
# -----------------------------------------------------------------------------
@app.get("/general")
@login_required
def general():
    """Dashboard unificado con resumen de hoy/semana, entrenamiento y finanzas."""
    uid = g.user["id"]
    hoy = date.today()
    modo = request.args.get("modo", "dia")
    dia_hoy_nombre = DIAS_SEMANA_COMPLETO[hoy.weekday()]

    # 1. Nutrición de hoy
    tot_nutri = q("""
        SELECT COALESCE(SUM(a.kcal * c.gramos / 100), 0) AS kcal,
               COALESCE(SUM(a.proteina * c.gramos / 100), 0) AS proteina,
               COALESCE(SUM(a.carbohidratos * c.gramos / 100), 0) AS carbohidratos,
               COALESCE(SUM(a.grasa * c.gramos / 100), 0) AS grasa
        FROM consumo c
        JOIN alimento a ON a.id = c.alimento_id
        WHERE c.user_id = %s AND c.fecha = CURRENT_DATE
    """, (uid,), one=True) or {"kcal": 0, "proteina": 0, "carbohidratos": 0, "grasa": 0}

    perfil = q("SELECT * FROM perfil_nutricional WHERE user_id = %s", (uid,), one=True) or {
        "meta_kcal": 2000, "meta_proteina": 140, "meta_carbos": 220,
        "meta_grasa": 60, "meta_agua_ml": 2000
    }

    reg_agua = q(
        "SELECT vasos FROM registro_agua WHERE user_id = %s AND fecha = CURRENT_DATE",
        (uid,),
        one=True
    )
    vasos_agua = reg_agua["vasos"] if reg_agua else 0
    agua_ml = vasos_agua * 250

    pct_kcal = min(round((float(tot_nutri["kcal"]) / float(perfil["meta_kcal"]) * 100)), 100) if perfil["meta_kcal"] > 0 else 0
    pct_prot = min(round((float(tot_nutri["proteina"]) / float(perfil["meta_proteina"]) * 100)), 100) if perfil["meta_proteina"] > 0 else 0
    pct_carb = min(round((float(tot_nutri["carbohidratos"]) / float(perfil["meta_carbos"]) * 100)), 100) if perfil["meta_carbos"] > 0 else 0
    pct_grasa = min(round((float(tot_nutri["grasa"]) / float(perfil["meta_grasa"]) * 100)), 100) if perfil["meta_grasa"] > 0 else 0
    pct_agua = min(round((agua_ml / float(perfil["meta_agua_ml"]) * 100)), 100) if perfil["meta_agua_ml"] > 0 else 0

    # 2. Nutrición semanal (Lunes a Domingo) con rangos indexables
    lunes_semana = hoy - timedelta(days=hoy.weekday())
    domingo_semana = lunes_semana + timedelta(days=6)
    fechas_semana = [lunes_semana + timedelta(days=i) for i in range(7)]

    consumos_semana = q("""
        SELECT c.fecha,
               ROUND(COALESCE(SUM(a.kcal * c.gramos / 100), 0)::numeric, 0) AS kcal,
               ROUND(COALESCE(SUM(a.proteina * c.gramos / 100), 0)::numeric, 1) AS proteina
        FROM consumo c
        JOIN alimento a ON a.id = c.alimento_id
        WHERE c.user_id = %s AND c.fecha >= %s AND c.fecha <= %s
        GROUP BY c.fecha
        ORDER BY c.fecha
    """, (uid, lunes_semana, domingo_semana))
    mapa_nutri_semana = {r["fecha"]: r for r in (consumos_semana or [])}

    chart_dias_labels = [f"{DIAS[d.weekday()]} {d.strftime('%d')}" for d in fechas_semana]
    chart_semana_kcal = [float(mapa_nutri_semana.get(d, {}).get("kcal", 0)) for d in fechas_semana]
    chart_semana_prot = [float(mapa_nutri_semana.get(d, {}).get("proteina", 0)) for d in fechas_semana]

    # 3. Entrenamiento (Deriva entrenado_hoy del set de la semana sin consulta extra)
    entrenamientos_hechos = set(r["fecha"] for r in (q("""
        SELECT fecha FROM entrenamiento
        WHERE user_id = %s AND fecha >= %s AND fecha <= %s
    """, (uid, lunes_semana, domingo_semana)) or []))

    entrenado_hoy = (hoy in entrenamientos_hechos)

    series_hoy = q("""
        SELECT COUNT(rs.id) AS total_series,
               COALESCE(SUM(rs.repeticiones), 0) AS total_reps,
               COALESCE(SUM(rs.peso_kg * rs.repeticiones), 0) AS volumen_kg
        FROM registro_serie rs
        WHERE rs.user_id = %s AND rs.fecha = CURRENT_DATE
    """, (uid,), one=True) or {"total_series": 0, "total_reps": 0, "volumen_kg": 0}

    plantillas_hoy = q("""
        SELECT d.id, d.nombre, d.dias_semana, COUNT(e.id) AS num_ejercicios
        FROM dia_rutina d
        LEFT JOIN ejercicio e ON e.dia_id = d.id
        WHERE d.user_id = %s AND d.dias_semana ILIKE %s
        GROUP BY d.id
    """, (uid, f"%{dia_hoy_nombre}%")) or []

    semana_entrenamiento = [
        {
            "dia_nombre": DIAS[d.weekday()],
            "dia_num": d.day,
            "fecha": d,
            "hecho": d in entrenamientos_hechos,
            "es_hoy": d == hoy
        }
        for d in fechas_semana
    ]
    dias_entrenados_semana = len(entrenamientos_hechos)

    volumen_semanal = q("""
        SELECT COALESCE(SUM(rs.peso_kg * rs.repeticiones), 0) AS total_volumen,
               COUNT(rs.id) AS total_series
        FROM registro_serie rs
        WHERE rs.user_id = %s AND rs.fecha >= %s AND rs.fecha <= %s
    """, (uid, lunes_semana, domingo_semana), one=True) or {"total_volumen": 0, "total_series": 0}

    # 4. Finanzas del mes (Rangos sargables con índices idx_ingreso_user_fecha y idx_egreso_user_fecha)
    y, m = hoy.year, hoy.month
    inicio_mes, fin_mes = get_month_range(y, m)

    row_ing = q("""
        SELECT COALESCE(SUM(monto), 0) AS tot
        FROM ingreso
        WHERE user_id = %s AND fecha >= %s AND fecha < %s
    """, (uid, inicio_mes, fin_mes), one=True)
    tot_ingresos = float(row_ing["tot"]) if row_ing else 0.0

    egresos_cat = q("""
        SELECT c.nombre, COALESCE(SUM(e.monto), 0) AS total
        FROM categoria_egreso c
        JOIN egreso e ON e.categoria_id = c.id
        WHERE c.user_id = %s AND e.fecha >= %s AND e.fecha < %s
        GROUP BY c.id, c.nombre
        ORDER BY total DESC
    """, (uid, inicio_mes, fin_mes)) or []

    # Optimización: el total de egresos se obtiene del agregado de categorías sin roundtrip extra
    tot_egresos = sum(float(r["total"]) for r in egresos_cat)
    balance = tot_ingresos - tot_egresos

    row_pto = q(
        "SELECT COALESCE(SUM(presupuesto_mensual), 0) AS tot FROM categoria_egreso WHERE user_id = %s",
        (uid,),
        one=True
    )
    total_presupuesto = float(row_pto["tot"]) if row_pto else 0.0
    pct_presupuesto = min(round((tot_egresos / total_presupuesto * 100)), 100) if total_presupuesto > 0 else 0

    chart_fin_labels = [r["nombre"] for r in egresos_cat]
    chart_fin_data = [float(r["total"]) for r in egresos_cat]

    ultimos_movimientos = q("""
        SELECT 'egreso' as tipo, c.nombre as clasificacion, e.concepto, e.monto, e.fecha
        FROM egreso e
        JOIN categoria_egreso c ON c.id = e.categoria_id
        WHERE e.user_id = %s
        UNION ALL
        SELECT 'ingreso' as tipo, f.nombre as clasificacion, 'Ingreso recibido' as concepto, i.monto, i.fecha
        FROM ingreso i
        JOIN fuente_ingreso f ON f.id = i.fuente_id
        WHERE i.user_id = %s
        ORDER BY fecha DESC
        LIMIT 4
    """, (uid, uid)) or []

    return render_template(
        "general.html",
        hoy=hoy,
        modo=modo,
        dia_hoy_nombre=dia_hoy_nombre,
        tot_nutri=tot_nutri,
        perfil=perfil,
        agua_ml=agua_ml,
        pct_kcal=pct_kcal,
        pct_prot=pct_prot,
        pct_carb=pct_carb,
        pct_grasa=pct_grasa,
        pct_agua=pct_agua,
        chart_dias_labels=chart_dias_labels,
        chart_semana_kcal=chart_semana_kcal,
        chart_semana_prot=chart_semana_prot,
        entrenado_hoy=entrenado_hoy,
        series_hoy=series_hoy,
        plantillas_hoy=plantillas_hoy,
        semana_entrenamiento=semana_entrenamiento,
        dias_entrenados_semana=dias_entrenados_semana,
        volumen_semanal=volumen_semanal,
        tot_ingresos=tot_ingresos,
        tot_egresos=tot_egresos,
        balance=balance,
        total_presupuesto=total_presupuesto,
        pct_presupuesto=pct_presupuesto,
        chart_fin_labels=chart_fin_labels,
        chart_fin_data=chart_fin_data,
        ultimos_movimientos=ultimos_movimientos,
        mes_nombre=f"{MESES[m-1]} {y}"
    )


# -----------------------------------------------------------------------------
# Módulo de Finanzas (Aislado por user_id)
# -----------------------------------------------------------------------------
@app.get("/finanzas")
@login_required
def finanzas():
    """Panel de finanzas personales sin purgas destructivas de historial."""
    uid = g.user["id"]
    hoy = date.today()
    inicio_mes, fin_mes = get_month_range(hoy.year, hoy.month)

    # 1. Fuentes de ingreso con sumas sargables
    fuentes = q("""
        SELECT f.id, f.nombre, COALESCE(SUM(i.monto), 0) AS total
        FROM fuente_ingreso f
        LEFT JOIN ingreso i ON i.fuente_id = f.id AND i.fecha >= %s AND i.fecha < %s
        WHERE f.user_id = %s
        GROUP BY f.id
        ORDER BY total DESC, f.nombre
    """, (inicio_mes, fin_mes, uid)) or []

    recientes_ingresos = q("""
        SELECT i.id, f.nombre, i.monto, i.fecha
        FROM ingreso i
        JOIN fuente_ingreso f ON f.id = i.fuente_id
        WHERE i.user_id = %s AND i.fecha >= %s AND i.fecha < %s
        ORDER BY i.fecha DESC, i.id DESC
        LIMIT 10
    """, (uid, inicio_mes, fin_mes)) or []

    total_ingresos = sum(float(f["total"]) for f in fuentes)

    # 2. Categorías de egreso con sumas sargables
    categorias_egreso = q("""
        SELECT c.id, c.nombre, COALESCE(c.presupuesto_mensual, 0) AS presupuesto,
               COALESCE(SUM(e.monto), 0) AS total,
               CASE WHEN COALESCE(c.presupuesto_mensual, 0) > 0 
                    THEN ROUND((COALESCE(SUM(e.monto), 0) / c.presupuesto_mensual * 100)::numeric, 1)
                    ELSE 0 END AS porcentaje
        FROM categoria_egreso c
        LEFT JOIN egreso e ON e.categoria_id = c.id AND e.fecha >= %s AND e.fecha < %s
        WHERE c.user_id = %s
        GROUP BY c.id
        ORDER BY total DESC, c.nombre
    """, (inicio_mes, fin_mes, uid)) or []

    recientes_egresos = q("""
        SELECT e.id, c.nombre AS categoria, e.concepto, e.monto, e.fecha
        FROM egreso e
        JOIN categoria_egreso c ON c.id = e.categoria_id
        WHERE e.user_id = %s AND e.fecha >= %s AND e.fecha < %s
        ORDER BY e.fecha DESC, e.id DESC
        LIMIT 10
    """, (uid, inicio_mes, fin_mes)) or []

    total_egresos = sum(float(c["total"]) for c in categorias_egreso)
    balance = total_ingresos - total_egresos

    # 3. Metas de ahorro del usuario
    metas_ahorro = q("""
        SELECT id, nombre, monto_objetivo, monto_actual, fecha_limite,
               CASE WHEN monto_objetivo > 0 
                    THEN LEAST(ROUND((monto_actual / monto_objetivo * 100)::numeric, 1), 100)
                    ELSE 0 END AS porcentaje
        FROM meta_ahorro
        WHERE user_id = %s
        ORDER BY id DESC
    """, (uid,)) or []

    # 4. Transacciones fijas recurrentes del usuario
    transacciones_fijas = q("""
        SELECT tf.id, tf.tipo, tf.concepto, tf.monto, tf.dia_mes,
               c.nombre AS categoria, f.nombre AS fuente
        FROM transaccion_fija tf
        LEFT JOIN categoria_egreso c ON c.id = tf.categoria_id
        LEFT JOIN fuente_ingreso f ON f.id = tf.fuente_id
        WHERE tf.user_id = %s
        ORDER BY tf.tipo, tf.dia_mes
    """, (uid,)) or []

    chart_egresos_labels = [c["nombre"] for c in categorias_egreso if float(c["total"]) > 0]
    chart_egresos_data = [float(c["total"]) for c in categorias_egreso if float(c["total"]) > 0]

    return render_template(
        "finanzas.html",
        fuentes=fuentes,
        recientes_ingresos=recientes_ingresos,
        total_ingresos=total_ingresos,
        categorias_egreso=categorias_egreso,
        recientes_egresos=recientes_egresos,
        total_egresos=total_egresos,
        balance=balance,
        metas_ahorro=metas_ahorro,
        transacciones_fijas=transacciones_fijas,
        hoy=hoy,
        chart_egresos_labels=chart_egresos_labels,
        chart_egresos_data=chart_egresos_data
    )


@app.post("/finanzas/ingreso")
@login_required
def add_ingreso():
    """Registra un nuevo ingreso validando la titularidad de la fuente (IDOR safe)."""
    uid = g.user["id"]
    form_data = request.form
    fuente_nueva = form_data.get("fuente_nueva", "").strip()
    monto = parse_float(form_data.get("monto"), 0.0, min_val=0.01)
    fecha = parse_date_str(form_data.get("fecha"))

    if monto <= 0:
        return redirect(url_for("finanzas"))

    if fuente_nueva:
        fuente_row = q("""
            INSERT INTO fuente_ingreso(user_id, nombre) VALUES (%s, %s)
            ON CONFLICT (user_id, nombre) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id
        """, (uid, fuente_nueva), one=True)
        fuente_id = fuente_row["id"]
    else:
        fuente_id = parse_int(form_data.get("fuente_id"), 0)
        fuente = q(
            "SELECT id FROM fuente_ingreso WHERE id = %s AND user_id = %s",
            (fuente_id, uid),
            one=True
        )
        if not fuente:
            logger.warning("Intento de IDOR en fuente_id %s por usuario %s", fuente_id, uid)
            return redirect(url_for("finanzas"))

    q(
        "INSERT INTO ingreso(user_id, fuente_id, monto, fecha) VALUES (%s, %s, %s, %s)",
        (uid, fuente_id, monto, fecha)
    )
    return redirect(url_for("finanzas"))


@app.post("/finanzas/ingreso/<int:id>/borrar")
@login_required
def del_ingreso(id):
    """Elimina un ingreso asegurando pertenencia al usuario activo."""
    q("DELETE FROM ingreso WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/egreso")
@login_required
def add_egreso():
    """Registra un nuevo egreso validando la titularidad de la categoría (IDOR safe)."""
    uid = g.user["id"]
    form_data = request.form
    cat_nueva = form_data.get("categoria_nueva", "").strip()
    monto = parse_float(form_data.get("monto"), 0.0, min_val=0.01)
    fecha = parse_date_str(form_data.get("fecha"))
    concepto = form_data.get("concepto", "").strip() or None

    if monto <= 0:
        return redirect(url_for("finanzas"))

    if cat_nueva:
        cat_row = q("""
            INSERT INTO categoria_egreso(user_id, nombre) VALUES (%s, %s)
            ON CONFLICT (user_id, nombre) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id
        """, (uid, cat_nueva), one=True)
        cat_id = cat_row["id"]
    else:
        cat_id = parse_int(form_data.get("categoria_id"), 0)
        cat = q(
            "SELECT id FROM categoria_egreso WHERE id = %s AND user_id = %s",
            (cat_id, uid),
            one=True
        )
        if not cat:
            logger.warning("Intento de IDOR en categoria_id %s por usuario %s", cat_id, uid)
            return redirect(url_for("finanzas"))

    q(
        "INSERT INTO egreso(user_id, categoria_id, concepto, monto, fecha) VALUES (%s, %s, %s, %s, %s)",
        (uid, cat_id, concepto, monto, fecha)
    )
    return redirect(url_for("finanzas"))


@app.post("/finanzas/egreso/<int:id>/borrar")
@login_required
def del_egreso(id):
    """Elimina un egreso asegurando pertenencia al usuario activo."""
    q("DELETE FROM egreso WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/presupuesto")
@login_required
def actualizar_presupuesto():
    """Actualiza el límite presupuestario de una categoría del usuario."""
    form_data = request.form
    cat_id = parse_int(form_data.get("categoria_id"), 0)
    monto = parse_float(form_data.get("presupuesto"), 0.0, min_val=0.0)
    if cat_id > 0:
        q(
            "UPDATE categoria_egreso SET presupuesto_mensual = %s WHERE id = %s AND user_id = %s",
            (monto, cat_id, g.user["id"])
        )
    return redirect(url_for("finanzas"))


@app.post("/finanzas/meta")
@login_required
def add_meta():
    """Crea una nueva meta de ahorro para el usuario."""
    form_data = request.form
    nombre = form_data.get("nombre", "").strip()
    objetivo = parse_float(form_data.get("monto_objetivo"), 0.0, min_val=0.01)
    actual = parse_float(form_data.get("monto_actual"), 0.0, min_val=0.0)
    fecha_limite = form_data.get("fecha_limite") or None

    if nombre and objetivo > 0:
        q("""
            INSERT INTO meta_ahorro(user_id, nombre, monto_objetivo, monto_actual, fecha_limite)
            VALUES (%s, %s, %s, %s, %s)
        """, (g.user["id"], nombre, objetivo, actual, fecha_limite))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/meta/<int:id>/abonar")
@login_required
def abonar_meta(id):
    """Incrementa los fondos ahorrados en una meta específica."""
    monto = parse_float(request.form.get("monto"), 0.0, min_val=0.01)
    if monto > 0:
        q(
            "UPDATE meta_ahorro SET monto_actual = monto_actual + %s WHERE id = %s AND user_id = %s",
            (monto, id, g.user["id"])
        )
    return redirect(url_for("finanzas"))


@app.post("/finanzas/meta/<int:id>/borrar")
@login_required
def del_meta(id):
    """Elimina una meta de ahorro del usuario."""
    q("DELETE FROM meta_ahorro WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/fija")
@login_required
def add_transaccion_fija():
    """Configura una transacción recurrente mensual con validación de titularidad."""
    uid = g.user["id"]
    form_data = request.form
    tipo = form_data.get("tipo", "egreso")
    if tipo not in ("ingreso", "egreso"):
        tipo = "egreso"

    concepto = form_data.get("concepto", "").strip()
    monto = parse_float(form_data.get("monto"), 0.0, min_val=0.01)
    dia_mes = parse_int(form_data.get("dia_mes"), 1, min_val=1, max_val=31)

    categoria_id = parse_int(form_data.get("categoria_id"), 0)
    if categoria_id > 0:
        cat = q("SELECT id FROM categoria_egreso WHERE id = %s AND user_id = %s", (categoria_id, uid), one=True)
        if not cat:
            categoria_id = None
    else:
        categoria_id = None

    fuente_id = parse_int(form_data.get("fuente_id"), 0)
    if fuente_id > 0:
        fuente = q("SELECT id FROM fuente_ingreso WHERE id = %s AND user_id = %s", (fuente_id, uid), one=True)
        if not fuente:
            fuente_id = None
    else:
        fuente_id = None

    if concepto and monto > 0:
        q("""
            INSERT INTO transaccion_fija(user_id, tipo, concepto, monto, dia_mes, categoria_id, fuente_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (uid, tipo, concepto, monto, dia_mes, categoria_id, fuente_id))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/fija/<int:id>/borrar")
@login_required
def del_transaccion_fija(id):
    """Elimina una transacción recurrente del usuario."""
    q("DELETE FROM transaccion_fija WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/fija/aplicar")
@login_required
def aplicar_fijas():
    """Aplica de forma atómica y vectorizada las transacciones fijas al mes actual."""
    uid = g.user["id"]
    q("""
        INSERT INTO egreso (user_id, categoria_id, concepto, monto, fecha)
        SELECT tf.user_id, tf.categoria_id, '[Fijo] ' || tf.concepto, tf.monto, CURRENT_DATE
        FROM transaccion_fija tf
        JOIN categoria_egreso c ON c.id = tf.categoria_id AND c.user_id = tf.user_id
        WHERE tf.user_id = %s AND tf.tipo = 'egreso' AND tf.categoria_id IS NOT NULL
    """, (uid,))

    q("""
        INSERT INTO ingreso (user_id, fuente_id, monto, fecha)
        SELECT tf.user_id, tf.fuente_id, tf.monto, CURRENT_DATE
        FROM transaccion_fija tf
        JOIN fuente_ingreso f ON f.id = tf.fuente_id AND f.user_id = tf.user_id
        WHERE tf.user_id = %s AND tf.tipo = 'ingreso' AND tf.fuente_id IS NOT NULL
    """, (uid,))

    return redirect(url_for("finanzas"))


@app.get("/finanzas/exportar")
@login_required
def exportar_finanzas():
    """Genera y descarga un reporte CSV con los movimientos del período activo."""
    uid = g.user["id"]
    hoy = date.today()
    inicio_mes, fin_mes = get_month_range(hoy.year, hoy.month)

    ingresos = q("""
        SELECT i.fecha, 'Ingreso' AS tipo, f.nombre AS clasificacion, '—' AS detalle, i.monto
        FROM ingreso i
        JOIN fuente_ingreso f ON f.id = i.fuente_id
        WHERE i.user_id = %s AND i.fecha >= %s AND i.fecha < %s
        ORDER BY i.fecha DESC
    """, (uid, inicio_mes, fin_mes)) or []

    egresos = q("""
        SELECT e.fecha, 'Egreso' AS tipo, c.nombre AS clasificacion, COALESCE(e.concepto, '—') AS detalle, e.monto
        FROM egreso e
        JOIN categoria_egreso c ON c.id = e.categoria_id
        WHERE e.user_id = %s AND e.fecha >= %s AND e.fecha < %s
        ORDER BY e.fecha DESC
    """, (uid, inicio_mes, fin_mes)) or []

    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(["Fecha", "Tipo", "Clasificacion", "Detalle", "Monto"])
    for row in sorted(ingresos + egresos, key=lambda x: str(x["fecha"]), reverse=True):
        escritor.writerow([row["fecha"], row["tipo"], row["clasificacion"], row["detalle"], row["monto"]])

    filename = f"reporte_finanzas_{hoy.strftime('%Y_%m')}.csv"
    return Response(
        salida.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# -----------------------------------------------------------------------------
# Módulo de Alimentación y Nutrición (Aislado por user_id)
# -----------------------------------------------------------------------------
@app.get("/alimentacion")
@login_required
def alimentacion():
    """Panel de nutrición y consumo sin purgas destructivas de registros pasados."""
    uid = g.user["id"]

    # 1. Perfil nutricional del usuario
    perfil = q("SELECT * FROM perfil_nutricional WHERE user_id = %s", (uid,), one=True)
    if not perfil:
        q("""
            INSERT INTO perfil_nutricional (user_id, meta_kcal, meta_proteina, meta_carbos, meta_grasa, meta_agua_ml)
            VALUES (%s, 2000, 140, 220, 60, 2000)
            ON CONFLICT (user_id) DO NOTHING
        """, (uid,))
        perfil = q("SELECT * FROM perfil_nutricional WHERE user_id = %s", (uid,), one=True)

    # 2. Hidratación del usuario
    reg_agua = q("SELECT vasos FROM registro_agua WHERE fecha = CURRENT_DATE AND user_id = %s", (uid,), one=True)
    vasos_agua = reg_agua["vasos"] if reg_agua else 0
    agua_ml = vasos_agua * 250
    meta_agua_ml = int(perfil.get("meta_agua_ml") or 2000)
    pct_agua = min(round((agua_ml / meta_agua_ml * 100)), 100) if meta_agua_ml > 0 else 0

    # 3. Consumo de hoy del usuario
    items = q("""
        SELECT c.id, a.nombre, a.categoria, a.unidad, a.gramos_unidad, c.gramos, c.porciones, c.momento,
               ROUND((a.kcal * c.gramos / 100)::numeric, 1) AS kcal,
               ROUND((a.proteina * c.gramos / 100)::numeric, 1) AS prot,
               ROUND((a.carbohidratos * c.gramos / 100)::numeric, 1) AS carb,
               ROUND((a.grasa * c.gramos / 100)::numeric, 1) AS grasa
        FROM consumo c
        JOIN alimento a ON a.id = c.alimento_id
        WHERE c.fecha = CURRENT_DATE AND c.user_id = %s
        ORDER BY c.id DESC
    """, (uid,)) or []

    comidas_por_momento = {m: [] for m in MOMENTOS_DIA}
    subtotales_momento = {m: {"kcal": 0, "prot": 0, "carb": 0, "grasa": 0} for m in MOMENTOS_DIA}

    for item in items:
        m = item["momento"] if item["momento"] in comidas_por_momento else "Almuerzo"
        comidas_por_momento[m].append(item)
        subtotales_momento[m]["kcal"] += float(item["kcal"])
        subtotales_momento[m]["prot"] += float(item["prot"])
        subtotales_momento[m]["carb"] += float(item["carb"])
        subtotales_momento[m]["grasa"] += float(item["grasa"])

    # Totales de hoy del usuario
    tot = q("""
        SELECT 
            ROUND(COALESCE(SUM(a.kcal * c.gramos / 100), 0)::numeric, 1) AS kcal,
            ROUND(COALESCE(SUM(a.proteina * c.gramos / 100), 0)::numeric, 1) AS proteina,
            ROUND(COALESCE(SUM(a.carbohidratos * c.gramos / 100), 0)::numeric, 1) AS carbohidratos,
            ROUND(COALESCE(SUM(a.grasa * c.gramos / 100), 0)::numeric, 1) AS grasa,
            ROUND(COALESCE(SUM((a.calcio_mg + a.hierro_mg + a.potasio_mg + a.vit_c_mg + a.vit_a_ug/1000.0 + a.vit_b12_ug/1000.0) * c.gramos / 100), 0)::numeric, 1) AS micros_mg
        FROM consumo c
        JOIN alimento a ON a.id = c.alimento_id
        WHERE c.fecha = CURRENT_DATE AND c.user_id = %s
    """, (uid,), one=True) or {"kcal": 0, "proteina": 0, "carbohidratos": 0, "grasa": 0, "micros_mg": 0}

    tot_micros_g = round(float(tot["micros_mg"] or 0) / 1000.0, 2)

    pct_kcal = min(round((float(tot["kcal"]) / float(perfil["meta_kcal"]) * 100)), 100) if perfil["meta_kcal"] > 0 else 0
    pct_prot = min(round((float(tot["proteina"]) / float(perfil["meta_proteina"]) * 100)), 100) if perfil["meta_proteina"] > 0 else 0
    pct_carb = min(round((float(tot["carbohidratos"]) / float(perfil["meta_carbos"]) * 100)), 100) if perfil["meta_carbos"] > 0 else 0
    pct_grasa = min(round((float(tot["grasa"]) / float(perfil["meta_grasa"]) * 100)), 100) if perfil["meta_grasa"] > 0 else 0

    # 4. Recetas guardadas del usuario
    recetas = q("""
        SELECT r.id, r.nombre, r.momento, COUNT(ri.id) AS num_items,
               ROUND(COALESCE(SUM(a.kcal * ri.gramos / 100), 0)::numeric, 0) AS kcal,
               ROUND(COALESCE(SUM(a.proteina * ri.gramos / 100), 0)::numeric, 1) AS prot
        FROM receta r
        LEFT JOIN receta_item ri ON ri.receta_id = r.id
        LEFT JOIN alimento a ON a.id = ri.alimento_id
        WHERE r.user_id = %s
        GROUP BY r.id
        ORDER BY r.nombre
    """, (uid,)) or []

    historial = q(
        "SELECT * FROM resumen_diario WHERE fecha < CURRENT_DATE AND user_id = %s ORDER BY fecha DESC",
        (uid,)
    ) or []
    categorias_alimentos = q("SELECT DISTINCT categoria FROM alimento ORDER BY categoria") or []

    return render_template(
        "alimentacion.html",
        items=items,
        comidas_por_momento=comidas_por_momento,
        subtotales_momento=subtotales_momento,
        momentos=MOMENTOS_DIA,
        tot=tot,
        tot_micros_g=tot_micros_g,
        perfil=perfil,
        pct_kcal=pct_kcal,
        pct_prot=pct_prot,
        pct_carb=pct_carb,
        pct_grasa=pct_grasa,
        vasos_agua=vasos_agua,
        agua_ml=agua_ml,
        meta_agua_ml=meta_agua_ml,
        pct_agua=pct_agua,
        recetas=recetas,
        historial=historial,
        categorias_alimentos=categorias_alimentos,
        err=request.args.get("err")
    )


@app.get("/alimentacion/buscar")
@login_required
def buscar_alimento():
    """Búsqueda autocompletada en catálogo oficial BEDCA y alimentos del usuario."""
    uid = g.user["id"]
    query = request.args.get("q", "").strip()
    palabras = query.split()
    if not palabras or sum(len(p) for p in palabras) < 2:
        return jsonify([])

    try:
        cond = " AND ".join(["unaccent(nombre) ILIKE unaccent(%s)"] * len(palabras))
        filas = q(f"""
            SELECT id, nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbohidratos, grasa
            FROM alimento
            WHERE (user_id IS NULL OR user_id = %s) AND {cond}
            ORDER BY (user_id = %s) DESC, length(nombre), nombre
            LIMIT 25
        """, [uid] + [f"%{p}%" for p in palabras] + [uid])
    except Exception as exc:
        logger.warning("Fallo en búsqueda unaccent, usando ILIKE estándar: %s", exc)
        cond = " AND ".join(["nombre ILIKE %s"] * len(palabras))
        filas = q(f"""
            SELECT id, nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbohidratos, grasa
            FROM alimento
            WHERE (user_id IS NULL OR user_id = %s) AND {cond}
            ORDER BY (user_id = %s) DESC, length(nombre), nombre
            LIMIT 25
        """, [uid] + [f"%{p}%" for p in palabras] + [uid])

    return jsonify(filas or [])


@app.post("/alimentacion/consumo")
@login_required
def add_consumo():
    """Registra el consumo de un alimento calculando macronutrientes proporcionales."""
    uid = g.user["id"]
    form_data = request.form
    nombre = form_data.get("alimento", "").strip()
    modo = form_data.get("modo", "porcion")
    momento = form_data.get("momento", "Almuerzo")
    if momento not in MOMENTOS_DIA:
        momento = "Almuerzo"

    cantidad = parse_float(form_data.get("cantidad"), 0.0, min_val=0.01)
    if not nombre or cantidad <= 0:
        return redirect(url_for("alimentacion", err=1))

    alimento_row = q("""
        SELECT id, nombre, unidad, gramos_unidad
        FROM alimento
        WHERE (user_id IS NULL OR user_id = %s)
          AND (lower(nombre) = lower(%s) OR nombre ILIKE %s)
        ORDER BY (user_id = %s) DESC, (lower(nombre) = lower(%s)) DESC, length(nombre)
        LIMIT 1
    """, (uid, nombre, f"%{nombre}%", uid, nombre), one=True)

    if not alimento_row:
        return redirect(url_for("alimentacion", err=1))

    gramos_ref = float(alimento_row["gramos_unidad"] or 100)
    if modo == "porcion":
        porciones = cantidad
        gramos = round(porciones * gramos_ref, 1)
    else:
        gramos = cantidad
        porciones = round(gramos / gramos_ref, 2) if gramos_ref > 0 else 1.0

    q(
        "INSERT INTO consumo(user_id, alimento_id, gramos, porciones, momento) VALUES (%s, %s, %s, %s, %s)",
        (uid, alimento_row["id"], gramos, porciones, momento)
    )
    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/consumo/<int:id>/borrar")
@login_required
def del_consumo(id):
    """Elimina un registro de consumo del usuario activo."""
    q("DELETE FROM consumo WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/perfil")
@login_required
def guardar_perfil():
    """Actualiza los objetivos diarios del perfil nutricional del usuario."""
    uid = g.user["id"]
    form_data = request.form
    meta_kcal = parse_float(form_data.get("meta_kcal"), 2000.0, min_val=500.0)
    meta_proteina = parse_float(form_data.get("meta_proteina"), 140.0, min_val=10.0)
    meta_carbos = parse_float(form_data.get("meta_carbos"), 220.0, min_val=10.0)
    meta_grasa = parse_float(form_data.get("meta_grasa"), 60.0, min_val=10.0)
    meta_agua_ml = parse_int(form_data.get("meta_agua_ml"), 2000, min_val=500)

    q("""
        INSERT INTO perfil_nutricional (user_id, meta_kcal, meta_proteina, meta_carbos, meta_grasa, meta_agua_ml)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            meta_kcal = EXCLUDED.meta_kcal,
            meta_proteina = EXCLUDED.meta_proteina,
            meta_carbos = EXCLUDED.meta_carbos,
            meta_grasa = EXCLUDED.meta_grasa,
            meta_agua_ml = EXCLUDED.meta_agua_ml
    """, (uid, meta_kcal, meta_proteina, meta_carbos, meta_grasa, meta_agua_ml))
    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/agua")
@login_required
def actualizar_agua():
    """Incrementa o decrementa los vasos de agua registrados en el día."""
    uid = g.user["id"]
    accion = request.form.get("accion", "sumar")
    if accion == "sumar":
        q("""
            INSERT INTO registro_agua (user_id, fecha, vasos) VALUES (%s, CURRENT_DATE, 1)
            ON CONFLICT (user_id, fecha) DO UPDATE SET vasos = registro_agua.vasos + 1
        """, (uid,))
    elif accion == "restar":
        q("""
            UPDATE registro_agua SET vasos = GREATEST(vasos - 1, 0)
            WHERE user_id = %s AND fecha = CURRENT_DATE
        """, (uid,))
    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/alimento/nuevo")
@login_required
def nuevo_alimento():
    """Crea o actualiza un alimento personalizado protegiendo el catálogo oficial BEDCA."""
    uid = g.user["id"]
    form_data = request.form
    nombre = form_data.get("nombre", "").strip()
    if not nombre:
        return redirect(url_for("alimentacion"))

    kcal = parse_float(form_data.get("kcal"), 0.0, min_val=0.0)
    proteina = parse_float(form_data.get("proteina"), 0.0, min_val=0.0)
    carbos = parse_float(form_data.get("carbohidratos"), 0.0, min_val=0.0)
    grasa = parse_float(form_data.get("grasa"), 0.0, min_val=0.0)
    categoria = form_data.get("categoria", "Otros").strip() or "Otros"
    unidad = form_data.get("unidad", "porción").strip() or "porción"
    gramos_unidad = parse_float(form_data.get("gramos_unidad"), 100.0, min_val=0.1)

    existente = q(
        "SELECT id, user_id FROM alimento WHERE lower(nombre) = lower(%s)",
        (nombre,),
        one=True
    )
    if existente:
        if existente["user_id"] is None:
            # Alimento oficial del catálogo BEDCA: protegido contra sobreescritura
            logger.info("Intento de sobreescritura de alimento BEDCA global bloqueado: %s", nombre)
            return redirect(url_for("alimentacion", err="No se puede modificar un alimento oficial del catálogo BEDCA."))
        elif existente["user_id"] != uid:
            # Alimento de otro usuario
            return redirect(url_for("alimentacion", err="Ya existe un alimento con ese nombre creado por otro usuario."))
        else:
            # Actualizar el alimento propio del usuario
            q("""
                UPDATE alimento SET
                    categoria = %s, unidad = %s, gramos_unidad = %s,
                    kcal = %s, proteina = %s, carbohidratos = %s, grasa = %s
                WHERE id = %s AND user_id = %s
            """, (categoria, unidad, gramos_unidad, kcal, proteina, carbos, grasa, existente["id"], uid))
    else:
        # Insertar nuevo alimento propio del usuario
        q("""
            INSERT INTO alimento (
                user_id, nombre, categoria, unidad, gramos_unidad,
                kcal, proteina, carbohidratos, grasa, fibra,
                calcio_mg, hierro_mg, potasio_mg, vit_c_mg, vit_a_ug, vit_b12_ug
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, 0, 0, 0, 0)
        """, (uid, nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbos, grasa))

    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/receta")
@login_required
def add_receta():
    """Crea una receta compuesta con inserción vectorizada de ingredientes."""
    uid = g.user["id"]
    form_data = request.form
    nombre = form_data.get("nombre", "").strip()
    momento = form_data.get("momento", "Desayuno")
    if momento not in MOMENTOS_DIA:
        momento = "Desayuno"

    if not nombre:
        return redirect(url_for("alimentacion"))

    items_json = form_data.get("items_json")
    consumir_ahora = (form_data.get("consumir_ahora") == "1")

    items_data = []
    if items_json:
        try:
            items_data = json.loads(items_json)
        except Exception:
            items_data = []
    else:
        alimento_nombre = form_data.get("alimento", "").strip()
        cantidad = parse_float(form_data.get("cantidad"), 1.0, min_val=0.01)
        modo = form_data.get("modo", "porcion")
        if alimento_nombre:
            a = q("""
                SELECT id, gramos_unidad FROM alimento
                WHERE (user_id IS NULL OR user_id = %s) AND nombre ILIKE %s
                LIMIT 1
            """, (uid, f"%{alimento_nombre}%"), one=True)
            if a:
                gramos = round(cantidad * float(a["gramos_unidad"] or 100), 1) if modo == "porcion" else cantidad
                items_data.append({
                    "alimento_id": a["id"],
                    "gramos": gramos,
                    "porciones": cantidad if modo == "porcion" else None
                })

    if not items_data:
        return redirect(url_for("alimentacion"))

    rid = q("""
        INSERT INTO receta (user_id, nombre, momento) VALUES (%s, %s, %s) RETURNING id
    """, (uid, nombre, momento), one=True)["id"]

    # Inserción en lote de items de receta
    filas_items = [
        (rid, parse_int(it["alimento_id"], 0), parse_float(it["gramos"], 100.0, min_val=0.1),
         parse_float(it["porciones"], None, min_val=0.01) if it.get("porciones") else None)
        for it in items_data if parse_int(it.get("alimento_id"), 0) > 0
    ]
    con = get_db()
    with con:
        with con.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO receta_item (receta_id, alimento_id, gramos, porciones) VALUES %s",
                filas_items
            )

    if consumir_ahora:
        q("""
            INSERT INTO consumo (user_id, alimento_id, gramos, porciones, momento, fecha)
            SELECT %s, alimento_id, gramos, porciones, %s, CURRENT_DATE
            FROM receta_item
            WHERE receta_id = %s
        """, (uid, momento, rid))

    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/receta/<int:id>/consumir")
@login_required
def consumir_receta(id):
    """Registra en consumo todos los constituyentes de una receta en una única consulta SQL."""
    uid = g.user["id"]
    receta_row = q("SELECT id, momento FROM receta WHERE id = %s AND user_id = %s", (id, uid), one=True)
    if not receta_row:
        return redirect(url_for("alimentacion"))

    q("""
        INSERT INTO consumo (user_id, alimento_id, gramos, porciones, momento, fecha)
        SELECT %s, alimento_id, gramos, porciones, %s, CURRENT_DATE
        FROM receta_item
        WHERE receta_id = %s
    """, (uid, receta_row["momento"], id))

    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/receta/<int:id>/borrar")
@login_required
def del_receta(id):
    """Elimina una receta del usuario activo (cascada automática a sus items)."""
    q("DELETE FROM receta WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("alimentacion"))


# -----------------------------------------------------------------------------
# Módulo de Entrenamiento (Aislado por user_id)
# -----------------------------------------------------------------------------
@app.get("/entrenamiento")
@login_required
def entrenamiento():
    """Panel de rutinas y series con optimización de subconsultas de récords personales."""
    uid = g.user["id"]
    hoy = date.today()
    dia_hoy_nombre = DIAS_SEMANA_COMPLETO[hoy.weekday()]

    mes_param = request.args.get("mes", "").strip()
    try:
        parts = mes_param.split("-")
        if len(parts) == 2:
            y, m = int(parts[0]), int(parts[1])
            if not (1 <= m <= 12 and 1900 <= y <= 2100):
                raise ValueError
        else:
            raise ValueError
        mes = mes_param
    except (ValueError, TypeError, AttributeError):
        y, m = hoy.year, hoy.month
        mes = hoy.strftime("%Y-%m")

    vista = request.args.get("vista", "todos")
    plantilla_id = request.args.get("plantilla_id")

    prev = (date(y, m, 1) - timedelta(days=1)).strftime("%Y-%m")
    nxt = (date(y, m, 28) + timedelta(days=4)).strftime("%Y-%m")

    semanas = [
        [date(y, m, d) if d else None for d in w]
        for w in calendar.Calendar(0).monthdayscalendar(y, m)
    ]
    lunes = hoy - timedelta(days=hoy.weekday())
    done = {r["fecha"] for r in (q("SELECT fecha FROM entrenamiento WHERE user_id = %s", (uid,)) or [])}

    dias_raw = q(
        "SELECT id, nombre, COALESCE(dias_semana, '') AS dias_semana FROM dia_rutina WHERE user_id = %s ORDER BY id",
        (uid,)
    ) or []

    dias_info = []
    dias_map = {}
    for d in dias_raw:
        lista = [x.strip() for x in d["dias_semana"].split(",") if x.strip()]
        es_hoy = (dia_hoy_nombre in lista)
        item = {
            "id": d["id"],
            "nombre": d["nombre"],
            "dias_semana": d["dias_semana"],
            "dias_lista": lista,
            "es_hoy": es_hoy
        }
        dias_info.append(item)
        dias_map[d["nombre"]] = item

    # Optimización: reemplazo de subconsulta correlacionada por LEFT JOIN a subconsulta agregada
    ejs = q("""
        SELECT e.*, d.nombre AS dia, d.id AS dia_id, pr.pr_peso
        FROM ejercicio e
        LEFT JOIN dia_rutina d ON d.id = e.dia_id
        LEFT JOIN (
            SELECT ejercicio_id, MAX(peso_kg) AS pr_peso
            FROM registro_serie
            WHERE user_id = %s
            GROUP BY ejercicio_id
        ) pr ON pr.ejercicio_id = e.id
        WHERE e.user_id = %s
        ORDER BY d.id NULLS LAST, e.id
    """, (uid, uid)) or []

    series_hoy = q("""
        SELECT id, ejercicio_id, numero_serie, peso_kg, repeticiones
        FROM registro_serie
        WHERE fecha = CURRENT_DATE AND user_id = %s AND (finalizada IS FALSE OR finalizada IS NULL)
        ORDER BY ejercicio_id, numero_serie
    """, (uid,)) or []
    total_series_activas = len(series_hoy)

    rutina_finalizada_hoy = bool(q("""
        SELECT 1 FROM registro_serie 
        WHERE user_id = %s AND fecha = CURRENT_DATE AND finalizada = TRUE 
        LIMIT 1
    """, (uid,), one=True))

    series_por_ejercicio = {}
    for s in series_hoy:
        series_por_ejercicio.setdefault(s["ejercicio_id"], []).append(s)

    secciones = {}
    for e in ejs:
        e["series_hoy"] = series_por_ejercicio.get(e["id"], [])
        e["siguiente_serie"] = len(e["series_hoy"]) + 1
        dia_nom = e["dia"] or "General"
        secciones.setdefault(dia_nom, []).append(e)

    if vista == "hoy":
        secciones_filtradas = {}
        for nom, items in secciones.items():
            info = dias_map.get(nom)
            if info and info["es_hoy"]:
                secciones_filtradas[nom] = items
        secciones = secciones_filtradas
    elif plantilla_id:
        p_obj = next((d for d in dias_info if str(d["id"]) == str(plantilla_id)), None)
        if p_obj and p_obj["nombre"] in secciones:
            secciones = {p_obj["nombre"]: secciones[p_obj["nombre"]]}

    historial_entrenamientos = q("""
        SELECT rs.fecha, e.nombre AS ejercicio, COUNT(rs.id) AS series_hechas,
               MAX(rs.peso_kg) AS peso_max, SUM(rs.repeticiones) AS total_reps
        FROM registro_serie rs
        JOIN ejercicio e ON e.id = rs.ejercicio_id
        WHERE (rs.fecha < CURRENT_DATE OR rs.finalizada = TRUE) AND rs.user_id = %s
        GROUP BY rs.fecha, e.nombre
        ORDER BY rs.fecha DESC, e.nombre
        LIMIT 20
    """, (uid,)) or []

    grupo = request.args.get("grupo")
    sugeridos = q(
        "SELECT * FROM catalogo_ejercicio WHERE grupo = %s ORDER BY id",
        (grupo,)
    ) if grupo else []

    return render_template(
        "entrenamiento.html",
        hoy=hoy,
        mes=mes,
        titulo=f"{MESES[m-1]} {y}",
        prev=prev,
        nxt=nxt,
        semanas=semanas,
        semana=[lunes + timedelta(days=i) for i in range(7)],
        done=done,
        dias_n=DIAS,
        secciones=secciones,
        dias=dias_info,
        dias_map=dias_map,
        vista=vista,
        plantilla_id=plantilla_id,
        dia_hoy_nombre=dia_hoy_nombre,
        dias_semana_nombres=DIAS_SEMANA_COMPLETO,
        total_series_activas=total_series_activas,
        rutina_finalizada_hoy=rutina_finalizada_hoy,
        grupo=grupo,
        grupos=q("SELECT DISTINCT grupo FROM catalogo_ejercicio ORDER BY grupo") or [],
        sugeridos=sugeridos,
        historial_entrenamientos=historial_entrenamientos
    )


@app.post("/entrenamiento/dia/<int:id>/dias")
@login_required
def asignar_dias_rutina(id):
    """Asigna los días de la semana correspondientes a una plantilla de rutina."""
    uid = g.user["id"]
    dias_sel = request.form.getlist("dias_semana")
    dias_str = ",".join(dias_sel)
    q(
        "UPDATE dia_rutina SET dias_semana = %s WHERE id = %s AND user_id = %s",
        (dias_str, id, uid)
    )
    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/finalizar")
@login_required
def finalizar_rutina():
    """Archiva las series activas de hoy y registra la fecha en el calendario."""
    uid = g.user["id"]
    q("INSERT INTO entrenamiento(user_id, fecha) VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (uid,))
    q("""
        UPDATE registro_serie 
        SET finalizada = TRUE 
        WHERE user_id = %s AND fecha = CURRENT_DATE AND (finalizada IS FALSE OR finalizada IS NULL)
    """, (uid,))

    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/limpiar-hoy")
@login_required
def limpiar_series_hoy():
    """Elimina las series no finalizadas registradas hoy para reiniciar el día."""
    uid = g.user["id"]
    q("""
        DELETE FROM registro_serie 
        WHERE user_id = %s AND fecha = CURRENT_DATE AND (finalizada IS FALSE OR finalizada IS NULL)
    """, (uid,))
    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/marcar")
@login_required
def marcar():
    """Alterna el estado completado de entrenamiento en una fecha del calendario."""
    uid = g.user["id"]
    fecha = request.form.get("fecha")
    mes = request.form.get("mes")
    if fecha:
        if not q("DELETE FROM entrenamiento WHERE fecha = %s AND user_id = %s RETURNING fecha", (fecha, uid)):
            q("INSERT INTO entrenamiento(user_id, fecha) VALUES (%s, %s)", (uid, fecha))
    return redirect(url_for("entrenamiento", mes=mes))


@app.post("/entrenamiento/serie")
@login_required
def add_serie():
    """Registra una nueva serie real. Soporta respuesta JSON para AJAX y redirección HTML."""
    uid = g.user["id"]
    form_data = request.form
    ejercicio_id = parse_int(form_data.get("ejercicio_id"), 0)
    numero_serie = parse_int(form_data.get("numero_serie"), 1, min_val=1)
    peso_kg = parse_float(form_data.get("peso_kg"), 0.0, min_val=0.0)
    repeticiones = parse_int(form_data.get("repeticiones"), 10, min_val=1)

    # Validar que el ejercicio pertenezca al usuario activo (IDOR safe)
    ejercicio = q("SELECT id FROM ejercicio WHERE id = %s AND user_id = %s", (ejercicio_id, uid), one=True)
    if not ejercicio:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Ejercicio no encontrado"}), 404
        return redirect(url_for("entrenamiento"))

    res = q("""
        INSERT INTO registro_serie(user_id, ejercicio_id, numero_serie, peso_kg, repeticiones, fecha, finalizada)
        VALUES (%s, %s, %s, %s, %s, CURRENT_DATE, FALSE)
        RETURNING id
    """, (uid, ejercicio_id, numero_serie, peso_kg, repeticiones), one=True)
    new_serie_id = res["id"] if res else None

    # Marcar el día automáticamente como entrenado en el calendario
    q("INSERT INTO entrenamiento(user_id, fecha) VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (uid,))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "ok": True,
            "serie_id": new_serie_id,
            "ejercicio_id": ejercicio_id,
            "numero_serie": numero_serie,
            "peso_kg": peso_kg,
            "repeticiones": repeticiones,
            "siguiente_serie": numero_serie + 1
        })

    vista = form_data.get("vista")
    plantilla_id = form_data.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/serie/<int:id>/borrar")
@login_required
def del_serie(id):
    """Elimina una serie activa. Soporta respuesta JSON para AJAX y redirección HTML."""
    q("DELETE FROM registro_serie WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "id": id})
    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/ejercicio")
@login_required
def add_ejercicio():
    """Añade un nuevo ejercicio a una rutina validando la titularidad de dia_id (IDOR safe)."""
    uid = g.user["id"]
    form_data = request.form
    nombre = form_data.get("nombre", "").strip()
    series = parse_int(form_data.get("series"), 3, min_val=1)
    repeticiones = parse_int(form_data.get("repeticiones"), 10, min_val=1)
    dia_id = parse_int(form_data.get("dia_id"), 0)

    if dia_id > 0:
        dia = q("SELECT id FROM dia_rutina WHERE id = %s AND user_id = %s", (dia_id, uid), one=True)
        if not dia:
            dia_id = None
    else:
        dia_id = None

    if nombre:
        q("""
            INSERT INTO ejercicio(user_id, nombre, series, repeticiones, dia_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (uid, nombre, series, repeticiones, dia_id))
    return redirect(url_for("entrenamiento"))


@app.post("/entrenamiento/ejercicio/<int:id>/editar")
@login_required
def edit_ejercicio(id):
    """Edita el nombre de un ejercicio. Soporta respuesta JSON para AJAX y redirección HTML."""
    uid = g.user["id"]
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        q("UPDATE ejercicio SET nombre = %s WHERE id = %s AND user_id = %s", (nombre, id, uid))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "id": id, "nombre": nombre})

    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/ejercicio/<int:id>/borrar")
@login_required
def del_ejercicio(id):
    """Elimina un ejercicio configurado en las plantillas del usuario."""
    q("DELETE FROM ejercicio WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("entrenamiento"))


@app.post("/entrenamiento/dia")
@login_required
def add_dia():
    """Crea una nueva plantilla de día de rutina."""
    uid = g.user["id"]
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        q("INSERT INTO dia_rutina(user_id, nombre) VALUES (%s, %s)", (uid, nombre))
    return redirect(url_for("entrenamiento"))


@app.post("/entrenamiento/dia/<int:id>/borrar")
@login_required
def del_dia(id):
    """Elimina una plantilla de día de rutina."""
    q("DELETE FROM dia_rutina WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("entrenamiento"))


# -----------------------------------------------------------------------------
# Punto de Entrada
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
