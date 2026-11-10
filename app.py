import calendar
import csv
import io
import json
import os
import secrets
from datetime import date, timedelta
from functools import wraps

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
from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash, generate_password_hash

# Carga nativa de variables de entorno locales (.env ignorado en git)
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

app = Flask(__name__)

# Protocolos de seguridad: Clave secreta persistente y configuración de cookies
secret_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
if not os.path.exists(secret_file):
    with open(secret_file, "w", encoding="utf-8") as f:
        f.write(secrets.token_hex(32))

with open(secret_file, "r", encoding="utf-8") as f:
    app.secret_key = os.getenv("SECRET_KEY", f.read().strip())

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Conexión a la base de datos (se lee desde variable de entorno DATABASE_URL)
DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/gestion")

MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DIAS_SEMANA_COMPLETO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MOMENTOS_DIA = ["Desayuno", "Almuerzo", "Cena", "Snack"]


def get_db():
    if not hasattr(g, "db") or g.db is None or g.db.closed:
        g.db = psycopg2.connect(DSN)
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db is not None and not db.closed:
        db.close()


def q(sql, args=(), one=False):
    if has_request_context():
        con = get_db()
        with con:
            with con.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, args)
                if cur.description:
                    rows = cur.fetchall()
                    return (rows[0] if rows else None) if one else rows
    else:
        con = psycopg2.connect(DSN)
        try:
            with con, con.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, args)
                if cur.description:
                    rows = cur.fetchall()
                    return (rows[0] if rows else None) if one else rows
        finally:
            con.close()


# --------------------------------------------------------------------------
# Protocolos de autenticación y autorización (Control de acceso)
# --------------------------------------------------------------------------
_schema_migrated = False

@app.before_request
def cargar_usuario():
    global _schema_migrated
    if not _schema_migrated:
        try:
            q("ALTER TABLE dia_rutina ADD COLUMN IF NOT EXISTS dias_semana TEXT DEFAULT '';")
            q("ALTER TABLE registro_serie ADD COLUMN IF NOT EXISTS finalizada BOOLEAN DEFAULT FALSE;")
            _schema_migrated = True
        except Exception:
            pass

    g.user = None
    uid = session.get("user_id")
    if uid:
        usuario = q("SELECT id, nombre, email FROM usuario WHERE id = %s", (uid,), one=True)
        if usuario:
            g.user = usuario
        else:
            session.clear()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/")
def inicio():
    if g.user:
        return redirect(url_for("general"))
    return redirect(url_for("login"))


@app.get("/general")
@login_required
def general():
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
        "meta_kcal": 2000, "meta_proteina": 140, "meta_carbos": 220, "meta_grasa": 60, "meta_agua_ml": 2000
    }

    reg_agua = q("SELECT vasos FROM registro_agua WHERE user_id = %s AND fecha = CURRENT_DATE", (uid,), one=True)
    vasos_agua = reg_agua["vasos"] if reg_agua else 0
    agua_ml = vasos_agua * 250

    pct_kcal = min(round((float(tot_nutri["kcal"]) / float(perfil["meta_kcal"]) * 100)), 100) if perfil["meta_kcal"] > 0 else 0
    pct_prot = min(round((float(tot_nutri["proteina"]) / float(perfil["meta_proteina"]) * 100)), 100) if perfil["meta_proteina"] > 0 else 0
    pct_carb = min(round((float(tot_nutri["carbohidratos"]) / float(perfil["meta_carbos"]) * 100)), 100) if perfil["meta_carbos"] > 0 else 0
    pct_grasa = min(round((float(tot_nutri["grasa"]) / float(perfil["meta_grasa"]) * 100)), 100) if perfil["meta_grasa"] > 0 else 0
    pct_agua = min(round((agua_ml / float(perfil["meta_agua_ml"]) * 100)), 100) if perfil["meta_agua_ml"] > 0 else 0

    # 2. Nutrición semanal (Lunes a Domingo)
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
    mapa_nutri_semana = {r["fecha"]: r for r in consumos_semana}

    chart_dias_labels = [f"{DIAS[d.weekday()]} {d.strftime('%d')}" for d in fechas_semana]
    chart_semana_kcal = [float(mapa_nutri_semana.get(d, {}).get("kcal", 0)) for d in fechas_semana]
    chart_semana_prot = [float(mapa_nutri_semana.get(d, {}).get("proteina", 0)) for d in fechas_semana]

    # 3. Entrenamiento
    entrenado_hoy = bool(q("SELECT 1 FROM entrenamiento WHERE user_id = %s AND fecha = CURRENT_DATE", (uid,), one=True))

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
    """, (uid, f"%{dia_hoy_nombre}%"))

    entrenamientos_hechos = set(r["fecha"] for r in q("""
        SELECT fecha FROM entrenamiento
        WHERE user_id = %s AND fecha >= %s AND fecha <= %s
    """, (uid, lunes_semana, domingo_semana)))

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

    # 4. Finanzas del mes
    mes_actual = hoy.strftime("%Y-%m")
    y, m = hoy.year, hoy.month
    tot_ingresos = float(q("SELECT COALESCE(SUM(monto), 0) AS tot FROM ingreso WHERE user_id = %s AND to_char(fecha, 'YYYY-MM') = %s", (uid, mes_actual), one=True)["tot"])
    tot_egresos = float(q("SELECT COALESCE(SUM(monto), 0) AS tot FROM egreso WHERE user_id = %s AND to_char(fecha, 'YYYY-MM') = %s", (uid, mes_actual), one=True)["tot"])
    balance = tot_ingresos - tot_egresos

    total_presupuesto = float(q("SELECT COALESCE(SUM(presupuesto_mensual), 0) AS tot FROM categoria_egreso WHERE user_id = %s", (uid,), one=True)["tot"])
    pct_presupuesto = min(round((tot_egresos / total_presupuesto * 100)), 100) if total_presupuesto > 0 else 0

    egresos_cat = q("""
        SELECT c.nombre, COALESCE(SUM(e.monto), 0) AS total
        FROM categoria_egreso c
        JOIN egreso e ON e.categoria_id = c.id
        WHERE c.user_id = %s AND to_char(e.fecha, 'YYYY-MM') = %s
        GROUP BY c.id, c.nombre
        ORDER BY total DESC
    """, (uid, mes_actual))
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
    """, (uid, uid))

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


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("general"))

    err = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        usuario = q("SELECT id, nombre, email, password_hash FROM usuario WHERE lower(email) = %s", (email,), one=True)
        if usuario and check_password_hash(usuario["password_hash"], password):
            session.clear()
            session["user_id"] = usuario["id"]
            recordar = request.form.get("recordar") == "1"
            session.permanent = recordar
            next_url = request.args.get("next") or url_for("general")
            return redirect(next_url)
        else:
            err = "Credenciales incorrectas. Verifica tu correo y contraseña."

    return render_template("login.html", err=err)


@app.route("/registro", methods=["GET", "POST"])
def registro():
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
            existente = q("SELECT id FROM usuario WHERE lower(email) = %s", (email,), one=True)
            if existente:
                err = "Ya existe una cuenta registrada con ese correo electrónico."
            else:
                pwd_hash = generate_password_hash(password)
                nuevo_id = q("""
                    INSERT INTO usuario (nombre, email, password_hash)
                    VALUES (%s, %s, %s) RETURNING id
                """, (nombre, email, pwd_hash), one=True)["id"]

                # Inicializar categorías de egreso base para el nuevo usuario
                cats_base = [
                    ('Alimentación y Super', 300), ('Vivienda y Alquiler', 500), ('Transporte', 100),
                    ('Servicios y Facturas', 120), ('Salud y Farmacia', 60), ('Educación', 80),
                    ('Ocio y Entretenimiento', 100), ('Ropa y Calzado', 50), ('Otros Gastos', 50)
                ]
                for cat_nombre, pto in cats_base:
                    q("INSERT INTO categoria_egreso (user_id, nombre, presupuesto_mensual) VALUES (%s, %s, %s)",
                      (nuevo_id, cat_nombre, pto))

                # Inicializar metas nutricionales base
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
    session.clear()
    return redirect(url_for("login"))


# ==========================================
# Finanzas (Aislado por user_id)
# ==========================================
@app.get("/finanzas")
@login_required
def finanzas():
    uid = g.user["id"]
    # Reinicio mensual automático para el usuario activo
    q("DELETE FROM ingreso WHERE fecha < date_trunc('month', CURRENT_DATE) AND user_id = %s", (uid,))
    q("DELETE FROM egreso WHERE fecha < date_trunc('month', CURRENT_DATE) AND user_id = %s", (uid,))

    # 1. Ingresos del usuario
    fuentes = q("""
        SELECT f.id, f.nombre, COALESCE(SUM(i.monto), 0) AS total
        FROM fuente_ingreso f
        LEFT JOIN ingreso i ON i.fuente_id = f.id AND date_trunc('month', i.fecha) = date_trunc('month', CURRENT_DATE)
        WHERE f.user_id = %s
        GROUP BY f.id
        ORDER BY total DESC, f.nombre
    """, (uid,))
    recientes_ingresos = q("""
        SELECT i.id, f.nombre, i.monto, i.fecha
        FROM ingreso i
        JOIN fuente_ingreso f ON f.id = i.fuente_id
        WHERE i.user_id = %s AND date_trunc('month', i.fecha) = date_trunc('month', CURRENT_DATE)
        ORDER BY i.fecha DESC, i.id DESC
        LIMIT 10
    """, (uid,))
    total_ingresos = q("""
        SELECT COALESCE(SUM(monto), 0) AS t FROM ingreso
        WHERE user_id = %s AND date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)
    """, (uid,), one=True)["t"]

    # 2. Egresos y Presupuestos del usuario
    categorias_egreso = q("""
        SELECT c.id, c.nombre, COALESCE(c.presupuesto_mensual, 0) AS presupuesto,
               COALESCE(SUM(e.monto), 0) AS total,
               CASE WHEN COALESCE(c.presupuesto_mensual, 0) > 0 
                    THEN ROUND((COALESCE(SUM(e.monto), 0) / c.presupuesto_mensual * 100)::numeric, 1)
                    ELSE 0 END AS porcentaje
        FROM categoria_egreso c
        LEFT JOIN egreso e ON e.categoria_id = c.id AND date_trunc('month', e.fecha) = date_trunc('month', CURRENT_DATE)
        WHERE c.user_id = %s
        GROUP BY c.id
        ORDER BY total DESC, c.nombre
    """, (uid,))
    recientes_egresos = q("""
        SELECT e.id, c.nombre AS categoria, e.concepto, e.monto, e.fecha
        FROM egreso e
        JOIN categoria_egreso c ON c.id = e.categoria_id
        WHERE e.user_id = %s AND date_trunc('month', e.fecha) = date_trunc('month', CURRENT_DATE)
        ORDER BY e.fecha DESC, e.id DESC
        LIMIT 10
    """, (uid,))
    total_egresos = q("""
        SELECT COALESCE(SUM(monto), 0) AS t FROM egreso
        WHERE user_id = %s AND date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)
    """, (uid,), one=True)["t"]

    balance = float(total_ingresos) - float(total_egresos)

    # 3. Metas de ahorro del usuario
    metas_ahorro = q("""
        SELECT id, nombre, monto_objetivo, monto_actual, fecha_limite,
               CASE WHEN monto_objetivo > 0 
                    THEN LEAST(ROUND((monto_actual / monto_objetivo * 100)::numeric, 1), 100)
                    ELSE 0 END AS porcentaje
        FROM meta_ahorro
        WHERE user_id = %s
        ORDER BY id DESC
    """, (uid,))

    # 4. Transacciones fijas recurrentes del usuario
    transacciones_fijas = q("""
        SELECT tf.id, tf.tipo, tf.concepto, tf.monto, tf.dia_mes,
               c.nombre AS categoria, f.nombre AS fuente
        FROM transaccion_fija tf
        LEFT JOIN categoria_egreso c ON c.id = tf.categoria_id
        LEFT JOIN fuente_ingreso f ON f.id = tf.fuente_id
        WHERE tf.user_id = %s
        ORDER BY tf.tipo, tf.dia_mes
    """, (uid,))

    # Datos para gráficos
    chart_egresos_labels = [c["nombre"] for c in categorias_egreso if c["total"] > 0]
    chart_egresos_data = [float(c["total"]) for c in categorias_egreso if c["total"] > 0]

    return render_template(
        "finanzas.html",
        fuentes=fuentes,
        recientes_ingresos=recientes_ingresos,
        total_ingresos=float(total_ingresos),
        categorias_egreso=categorias_egreso,
        recientes_egresos=recientes_egresos,
        total_egresos=float(total_egresos),
        balance=balance,
        metas_ahorro=metas_ahorro,
        transacciones_fijas=transacciones_fijas,
        hoy=date.today(),
        chart_egresos_labels=chart_egresos_labels,
        chart_egresos_data=chart_egresos_data
    )


@app.post("/finanzas/ingreso")
@login_required
def add_ingreso():
    uid = g.user["id"]
    f = request.form
    nueva = f.get("fuente_nueva", "").strip()
    if nueva:
        fid = q("""
            INSERT INTO fuente_ingreso(user_id, nombre) VALUES (%s, %s)
            ON CONFLICT (user_id, nombre) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id
        """, (uid, nueva), one=True)["id"]
    elif f.get("fuente_id"):
        fid = int(f["fuente_id"])
    else:
        return redirect(url_for("finanzas"))
    q("INSERT INTO ingreso(user_id, fuente_id, monto, fecha) VALUES (%s,%s,%s,%s)", (uid, fid, f["monto"], f["fecha"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/ingreso/<int:id>/borrar")
@login_required
def del_ingreso(id):
    q("DELETE FROM ingreso WHERE id=%s AND user_id=%s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/egreso")
@login_required
def add_egreso():
    uid = g.user["id"]
    f = request.form
    nueva = f.get("categoria_nueva", "").strip()
    if nueva:
        cid = q("""
            INSERT INTO categoria_egreso(user_id, nombre) VALUES (%s, %s)
            ON CONFLICT (user_id, nombre) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id
        """, (uid, nueva), one=True)["id"]
    elif f.get("categoria_id"):
        cid = int(f["categoria_id"])
    else:
        return redirect(url_for("finanzas"))
    concepto = f.get("concepto", "").strip() or None
    q("INSERT INTO egreso(user_id, categoria_id, concepto, monto, fecha) VALUES (%s,%s,%s,%s,%s)",
      (uid, cid, concepto, f["monto"], f["fecha"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/egreso/<int:id>/borrar")
@login_required
def del_egreso(id):
    q("DELETE FROM egreso WHERE id=%s AND user_id=%s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/presupuesto")
@login_required
def actualizar_presupuesto():
    f = request.form
    cid = f.get("categoria_id")
    monto = float(f.get("presupuesto", 0) or 0)
    if cid:
        q("UPDATE categoria_egreso SET presupuesto_mensual = %s WHERE id = %s AND user_id = %s",
          (monto, int(cid), g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/meta")
@login_required
def add_meta():
    f = request.form
    nombre = f.get("nombre", "").strip()
    objetivo = float(f.get("monto_objetivo", 0) or 0)
    actual = float(f.get("monto_actual", 0) or 0)
    fecha_limite = f.get("fecha_limite") or None
    if nombre and objetivo > 0:
        q("""
            INSERT INTO meta_ahorro(user_id, nombre, monto_objetivo, monto_actual, fecha_limite)
            VALUES (%s,%s,%s,%s,%s)
        """, (g.user["id"], nombre, objetivo, actual, fecha_limite))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/meta/<int:id>/abonar")
@login_required
def abonar_meta(id):
    monto = float(request.form.get("monto", 0) or 0)
    if monto > 0:
        q("UPDATE meta_ahorro SET monto_actual = monto_actual + %s WHERE id = %s AND user_id = %s",
          (monto, id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/meta/<int:id>/borrar")
@login_required
def del_meta(id):
    q("DELETE FROM meta_ahorro WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/fija")
@login_required
def add_transaccion_fija():
    f = request.form
    tipo = f.get("tipo", "egreso")
    concepto = f.get("concepto", "").strip()
    monto = float(f.get("monto", 0) or 0)
    dia_mes = int(f.get("dia_mes", 1) or 1)
    categoria_id = int(f.get("categoria_id")) if f.get("categoria_id") else None
    fuente_id = int(f.get("fuente_id")) if f.get("fuente_id") else None

    if concepto and monto > 0:
        q("""
            INSERT INTO transaccion_fija(user_id, tipo, concepto, monto, dia_mes, categoria_id, fuente_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (g.user["id"], tipo, concepto, monto, dia_mes, categoria_id, fuente_id))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/fija/<int:id>/borrar")
@login_required
def del_transaccion_fija(id):
    q("DELETE FROM transaccion_fija WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("finanzas"))


@app.post("/finanzas/fija/aplicar")
@login_required
def aplicar_fijas():
    uid = g.user["id"]
    fijas = q("SELECT * FROM transaccion_fija WHERE user_id = %s", (uid,))
    hoy = date.today()
    for f in fijas:
        if f["tipo"] == "egreso" and f["categoria_id"]:
            q("INSERT INTO egreso(user_id, categoria_id, concepto, monto, fecha) VALUES (%s,%s,%s,%s,%s)",
              (uid, f["categoria_id"], f"[Fijo] {f['concepto']}", f["monto"], hoy))
        elif f["tipo"] == "ingreso" and f["fuente_id"]:
            q("INSERT INTO ingreso(user_id, fuente_id, monto, fecha) VALUES (%s,%s,%s,%s)",
              (uid, f["fuente_id"], f["monto"], hoy))
    return redirect(url_for("finanzas"))


@app.get("/finanzas/exportar")
@login_required
def exportar_finanzas():
    uid = g.user["id"]
    ingresos = q("""
        SELECT i.fecha, 'Ingreso' AS tipo, f.nombre AS clasificacion, '—' AS detalle, i.monto
        FROM ingreso i JOIN fuente_ingreso f ON f.id = i.fuente_id
        WHERE i.user_id = %s AND date_trunc('month', i.fecha) = date_trunc('month', CURRENT_DATE)
        ORDER BY i.fecha DESC
    """, (uid,))
    egresos = q("""
        SELECT e.fecha, 'Egreso' AS tipo, c.nombre AS clasificacion, COALESCE(e.concepto, '—') AS detalle, e.monto
        FROM egreso e JOIN categoria_egreso c ON c.id = e.categoria_id
        WHERE e.user_id = %s AND date_trunc('month', e.fecha) = date_trunc('month', CURRENT_DATE)
        ORDER BY e.fecha DESC
    """, (uid,))

    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(["Fecha", "Tipo", "Clasificacion", "Detalle", "Monto"])
    for r in sorted(ingresos + egresos, key=lambda x: str(x["fecha"]), reverse=True):
        escritor.writerow([r["fecha"], r["tipo"], r["clasificacion"], r["detalle"], r["monto"]])

    return Response(
        salida.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=reporte_finanzas_{date.today().strftime('%Y_%m')}.csv"}
    )


# ==========================================
# Alimentación (Aislado por user_id)
# ==========================================
@app.get("/alimentacion")
@login_required
def alimentacion():
    uid = g.user["id"]
    # Reinicio mensual de consumos para el usuario activo
    q("DELETE FROM consumo WHERE fecha < date_trunc('month', CURRENT_DATE) AND user_id = %s", (uid,))

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
    """, (uid,))

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
    """, (uid,), one=True)

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
    """, (uid,))

    historial = q("SELECT * FROM resumen_diario WHERE fecha < CURRENT_DATE AND user_id = %s ORDER BY fecha DESC", (uid,))
    categorias_alimentos = q("SELECT DISTINCT categoria FROM alimento ORDER BY categoria")

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
    palabras = request.args.get("q", "").strip().split()
    if not palabras or sum(len(p) for p in palabras) < 2:
        return jsonify([])
    try:
        cond = " AND ".join(["unaccent(nombre) ILIKE unaccent(%s)"] * len(palabras))
        filas = q(f"""
            SELECT id, nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbohidratos, grasa
            FROM alimento
            WHERE {cond}
            ORDER BY length(nombre), nombre
            LIMIT 25
        """, [f"%{p}%" for p in palabras])
    except Exception:
        cond = " AND ".join(["nombre ILIKE %s"] * len(palabras))
        filas = q(f"""
            SELECT id, nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbohidratos, grasa
            FROM alimento
            WHERE {cond}
            ORDER BY length(nombre), nombre
            LIMIT 25
        """, [f"%{p}%" for p in palabras])
    return jsonify(filas)


@app.post("/alimentacion/consumo")
@login_required
def add_consumo():
    uid = g.user["id"]
    f = request.form
    nombre = f.get("alimento", "").strip()
    modo = f.get("modo", "porcion")
    momento = f.get("momento", "Almuerzo")
    if momento not in MOMENTOS_DIA:
        momento = "Almuerzo"
    
    try:
        cantidad = float(f.get("cantidad", 0))
    except (ValueError, TypeError):
        cantidad = 0

    if not nombre or cantidad <= 0:
        return redirect(url_for("alimentacion", err=1))

    a = q("""
        SELECT id, nombre, unidad, gramos_unidad
        FROM alimento
        WHERE lower(nombre) = lower(%s) OR nombre ILIKE %s
        ORDER BY lower(nombre) = lower(%s) DESC, length(nombre)
        LIMIT 1
    """, (nombre, f"%{nombre}%", nombre), one=True)

    if not a:
        return redirect(url_for("alimentacion", err=1))

    gramos_ref = float(a["gramos_unidad"] or 100)
    if modo == "porcion":
        porciones = cantidad
        gramos = round(porciones * gramos_ref, 1)
    else:
        gramos = cantidad
        porciones = round(gramos / gramos_ref, 2) if gramos_ref > 0 else 1.0

    q("INSERT INTO consumo(user_id, alimento_id, gramos, porciones, momento) VALUES (%s,%s,%s,%s,%s)",
      (uid, a["id"], gramos, porciones, momento))
    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/consumo/<int:id>/borrar")
@login_required
def del_consumo(id):
    q("DELETE FROM consumo WHERE id=%s AND user_id=%s", (id, g.user["id"]))
    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/perfil")
@login_required
def guardar_perfil():
    uid = g.user["id"]
    f = request.form
    meta_kcal = float(f.get("meta_kcal", 2000) or 2000)
    meta_proteina = float(f.get("meta_proteina", 140) or 140)
    meta_carbos = float(f.get("meta_carbos", 220) or 220)
    meta_grasa = float(f.get("meta_grasa", 60) or 60)
    meta_agua_ml = int(f.get("meta_agua_ml", 2000) or 2000)

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
    f = request.form
    nombre = f.get("nombre", "").strip()
    if not nombre:
        return redirect(url_for("alimentacion"))

    kcal = float(f.get("kcal", 0) or 0)
    proteina = float(f.get("proteina", 0) or 0)
    carbos = float(f.get("carbohidratos", 0) or 0)
    grasa = float(f.get("grasa", 0) or 0)
    categoria = f.get("categoria", "Otros").strip() or "Otros"
    unidad = f.get("unidad", "porción").strip() or "porción"
    gramos_unidad = float(f.get("gramos_unidad", 100) or 100)

    q("""
        INSERT INTO alimento (nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbohidratos, grasa, fibra, calcio_mg, hierro_mg, potasio_mg, vit_c_mg, vit_a_ug, vit_b12_ug)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, 0, 0, 0, 0)
        ON CONFLICT (nombre) DO UPDATE SET
            categoria = EXCLUDED.categoria,
            unidad = EXCLUDED.unidad,
            gramos_unidad = EXCLUDED.gramos_unidad,
            kcal = EXCLUDED.kcal,
            proteina = EXCLUDED.proteina,
            carbohidratos = EXCLUDED.carbohidratos,
            grasa = EXCLUDED.grasa
    """, (nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbos, grasa))

    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/receta")
@login_required
def add_receta():
    uid = g.user["id"]
    f = request.form
    nombre = f.get("nombre", "").strip()
    momento = f.get("momento", "Desayuno")
    if not nombre:
        return redirect(url_for("alimentacion"))

    items_json = f.get("items_json")
    consumir_ahora = f.get("consumir_ahora") == "1"

    items_data = []
    if items_json:
        try:
            items_data = json.loads(items_json)
        except Exception:
            items_data = []
    else:
        # Compatibilidad con formulario individual
        alimento_nombre = f.get("alimento", "").strip()
        cantidad = float(f.get("cantidad", 1) or 1)
        modo = f.get("modo", "porcion")
        if alimento_nombre:
            a = q("SELECT id, gramos_unidad FROM alimento WHERE nombre ILIKE %s LIMIT 1", (f"%{alimento_nombre}%",), one=True)
            if a:
                gramos = round(cantidad * float(a["gramos_unidad"]), 1) if modo == "porcion" else cantidad
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

    for it in items_data:
        q("INSERT INTO receta_item (receta_id, alimento_id, gramos, porciones) VALUES (%s, %s, %s, %s)",
          (rid, int(it["alimento_id"]), float(it["gramos"]), float(it["porciones"]) if it.get("porciones") else None))
        if consumir_ahora:
            q("INSERT INTO consumo (user_id, alimento_id, gramos, porciones, momento, fecha) VALUES (%s, %s, %s, %s, %s, CURRENT_DATE)",
              (uid, int(it["alimento_id"]), float(it["gramos"]), float(it["porciones"]) if it.get("porciones") else None, momento))

    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/receta/<int:id>/consumir")
@login_required
def consumir_receta(id):
    uid = g.user["id"]
    r = q("SELECT * FROM receta WHERE id = %s AND user_id = %s", (id, uid), one=True)
    if not r:
        return redirect(url_for("alimentacion"))

    items = q("SELECT * FROM receta_item WHERE receta_id = %s", (id,))
    for it in items:
        q("INSERT INTO consumo(user_id, alimento_id, gramos, porciones, momento) VALUES (%s,%s,%s,%s,%s)",
          (uid, it["alimento_id"], it["gramos"], it["porciones"], r["momento"]))
    return redirect(url_for("alimentacion"))


@app.post("/alimentacion/receta/<int:id>/borrar")
@login_required
def del_receta(id):
    q("DELETE FROM receta WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    return redirect(url_for("alimentacion"))


# ==========================================
# Entrenamiento (Aislado por user_id)
# ==========================================
@app.get("/entrenamiento")
@login_required
def entrenamiento():
    uid = g.user["id"]
    hoy = date.today()
    dia_hoy_nombre = DIAS_SEMANA_COMPLETO[hoy.weekday()]
    mes = request.args.get("mes") or hoy.strftime("%Y-%m")
    vista = request.args.get("vista", "todos")
    plantilla_id = request.args.get("plantilla_id")
    y, m = map(int, mes.split("-"))
    prev = (date(y, m, 1) - timedelta(days=1)).strftime("%Y-%m")
    nxt = (date(y, m, 28) + timedelta(days=4)).strftime("%Y-%m")

    semanas = [
        [date(y, m, d) if d else None for d in w]
        for w in calendar.Calendar(0).monthdayscalendar(y, m)
    ]
    lunes = hoy - timedelta(days=hoy.weekday())
    done = {r["fecha"] for r in q("SELECT fecha FROM entrenamiento WHERE user_id = %s", (uid,))}

    dias_raw = q("SELECT id, nombre, COALESCE(dias_semana, '') AS dias_semana FROM dia_rutina WHERE user_id = %s ORDER BY id", (uid,))
    dias_info = []
    dias_map = {}
    for d in dias_raw:
        lista = [x.strip() for x in d["dias_semana"].split(",") if x.strip()]
        es_hoy = dia_hoy_nombre in lista
        item = {
            "id": d["id"],
            "nombre": d["nombre"],
            "dias_semana": d["dias_semana"],
            "dias_lista": lista,
            "es_hoy": es_hoy
        }
        dias_info.append(item)
        dias_map[d["nombre"]] = item

    # Ejercicios con su Récord Personal histórico (PR) del usuario
    ejs = q("""
        SELECT e.*, d.nombre AS dia, d.id AS dia_id,
               (SELECT MAX(peso_kg) FROM registro_serie WHERE ejercicio_id = e.id AND user_id = %s) AS pr_peso
        FROM ejercicio e
        LEFT JOIN dia_rutina d ON d.id = e.dia_id
        WHERE e.user_id = %s
        ORDER BY d.id NULLS LAST, e.id
    """, (uid, uid))

    # Series registradas hoy por el usuario (solo las activas / no finalizadas)
    series_hoy = q("""
        SELECT id, ejercicio_id, numero_serie, peso_kg, repeticiones
        FROM registro_serie
        WHERE fecha = CURRENT_DATE AND user_id = %s AND (finalizada IS FALSE OR finalizada IS NULL)
        ORDER BY ejercicio_id, numero_serie
    """, (uid,))
    total_series_activas = len(series_hoy)

    # Verificar si el usuario ya finalizó su rutina de hoy
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

    # Filtrado según la vista seleccionada (hoy o plantilla específica)
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

    # Historial de sesiones anteriores o rutinas ya finalizadas hoy
    historial_entrenamientos = q("""
        SELECT rs.fecha, e.nombre AS ejercicio, COUNT(rs.id) AS series_hechas,
               MAX(rs.peso_kg) AS peso_max, SUM(rs.repeticiones) AS total_reps
        FROM registro_serie rs
        JOIN ejercicio e ON e.id = rs.ejercicio_id
        WHERE (rs.fecha < CURRENT_DATE OR rs.finalizada = TRUE) AND rs.user_id = %s
        GROUP BY rs.fecha, e.nombre
        ORDER BY rs.fecha DESC, e.nombre
        LIMIT 20
    """, (uid,))

    grupo = request.args.get("grupo")
    sugeridos = q("SELECT * FROM catalogo_ejercicio WHERE grupo=%s ORDER BY id", (grupo,)) if grupo else []

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
        grupos=q("SELECT DISTINCT grupo FROM catalogo_ejercicio ORDER BY grupo"),
        sugeridos=sugeridos,
        historial_entrenamientos=historial_entrenamientos
    )


@app.post("/entrenamiento/dia/<int:id>/dias")
@login_required
def asignar_dias_rutina(id):
    uid = g.user["id"]
    dias_sel = request.form.getlist("dias_semana")
    dias_str = ",".join(dias_sel)
    q("UPDATE dia_rutina SET dias_semana = %s WHERE id = %s AND user_id = %s",
      (dias_str, id, uid))
    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/finalizar")
@login_required
def finalizar_rutina():
    uid = g.user["id"]
    # 1. Asegurar registro del día entrenado en el calendario
    q("INSERT INTO entrenamiento(user_id, fecha) VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (uid,))
    
    # 2. Finalizar todas las series activas de hoy (se archivan en el historial y se limpia la plantilla)
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
    uid = g.user["id"]
    # Borrar permanentemente las series registradas hoy si se solicita restablecer la plantilla
    q("DELETE FROM registro_serie WHERE user_id = %s AND fecha = CURRENT_DATE", (uid,))
    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/marcar")
@login_required
def marcar():
    uid = g.user["id"]
    fecha = request.form["fecha"]
    mes = request.form.get("mes")
    if not q("DELETE FROM entrenamiento WHERE fecha=%s AND user_id=%s RETURNING fecha", (fecha, uid)):
        q("INSERT INTO entrenamiento(user_id, fecha) VALUES (%s, %s)", (uid, fecha))
    return redirect(url_for("entrenamiento", mes=mes))


@app.post("/entrenamiento/serie")
@login_required
def add_serie():
    uid = g.user["id"]
    f = request.form
    ejercicio_id = int(f.get("ejercicio_id"))
    numero_serie = int(f.get("numero_serie", 1) or 1)
    peso_kg = float(f.get("peso_kg", 0) or 0)
    repeticiones = int(f.get("repeticiones", 10) or 10)

    # Validar que el ejercicio pertenezca al usuario activo
    e = q("SELECT id FROM ejercicio WHERE id = %s AND user_id = %s", (ejercicio_id, uid), one=True)
    if not e:
        return redirect(url_for("entrenamiento"))

    q("""
        INSERT INTO registro_serie(user_id, ejercicio_id, numero_serie, peso_kg, repeticiones, fecha, finalizada)
        VALUES (%s, %s, %s, %s, %s, CURRENT_DATE, FALSE)
    """, (uid, ejercicio_id, numero_serie, peso_kg, repeticiones))

    # Marcar el día automáticamente como entrenado en el calendario del usuario
    q("INSERT INTO entrenamiento(user_id, fecha) VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (uid,))
    vista = f.get("vista")
    plantilla_id = f.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/serie/<int:id>/borrar")
@login_required
def del_serie(id):
    q("DELETE FROM registro_serie WHERE id = %s AND user_id = %s", (id, g.user["id"]))
    vista = request.form.get("vista")
    plantilla_id = request.form.get("plantilla_id")
    return redirect(url_for("entrenamiento", vista=vista, plantilla_id=plantilla_id))


@app.post("/entrenamiento/ejercicio")
@login_required
def add_ejercicio():
    uid = g.user["id"]
    f = request.form
    dia_id = f.get("dia_id")
    q("INSERT INTO ejercicio(user_id, nombre, series, repeticiones, dia_id) VALUES (%s,%s,%s,%s,%s)",
      (uid, f["nombre"].strip(), f["series"], f["repeticiones"], int(dia_id) if dia_id else None))
    return redirect(url_for("entrenamiento"))


@app.post("/entrenamiento/ejercicio/<int:id>/borrar")
@login_required
def del_ejercicio(id):
    q("DELETE FROM ejercicio WHERE id=%s AND user_id=%s", (id, g.user["id"]))
    return redirect(url_for("entrenamiento"))


@app.post("/entrenamiento/dia")
@login_required
def add_dia():
    uid = g.user["id"]
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        q("INSERT INTO dia_rutina(user_id, nombre) VALUES (%s, %s)", (uid, nombre))
    return redirect(url_for("entrenamiento"))


@app.post("/entrenamiento/dia/<int:id>/borrar")
@login_required
def del_dia(id):
    q("DELETE FROM dia_rutina WHERE id=%s AND user_id=%s", (id, g.user["id"]))
    return redirect(url_for("entrenamiento"))


if __name__ == "__main__":
    app.run(debug=True)
