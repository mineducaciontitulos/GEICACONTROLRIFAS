import os
import uuid
import random
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, send_from_directory, jsonify, abort
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Módulos internos
from notificaciones.notificaciones import enviar_whatsapp, enviar_correo
from pagos.wompi import generar_link_de_pago, verificar_evento_webhook

# 🔑 Token de acceso para Superadmin
SUPERADMIN_TOKEN = "geica-dev"  # cámbialo si quieres otro valor

# ================== CONFIG INICIAL ==================
load_dotenv()

BASE_DIR   = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "geica-super-secret")

# ================== CONEXIÓN DB (PostgreSQL) ==================
def db():
    """
    Conecta a PostgreSQL usando DATABASE_URL.
    Agrega sslmode=require si no viene.
    """
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL no está configurada. Defínela en Render.")
    if "sslmode=" not in db_url:
        sep = "&" if "?" in db_url else "?"
        db_url = db_url + f"{sep}sslmode=require"
    return psycopg2.connect(db_url)

def negocio_actual():
    """Retorna el registro del negocio logueado (dict) o None."""
    nid = session.get("negocio_id")
    if not nid:
        return None
    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM negocios WHERE id = %s", (nid,))
    row = cur.fetchone()
    con.close()
    return row

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def numero_estado_css(estado: str) -> str:
    return {
        "disponible": "disponible",
        "reservado": "reservado",
        "pagado": "pagado"
    }.get(estado, "disponible")

def generar_numeros(cifras: int, cantidad: int):
    """
    2 cifras -> 00..99 (ordenado, fijo 100)
    3/4 cifras -> lista aleatoria de longitud 'cantidad'
    """
    if cifras == 2:
        return [f"{i:02d}" for i in range(100)]
    maximo = 10 ** cifras
    todos = [f"{i:0{cifras}d}" for i in range(maximo)]
    random.shuffle(todos)
    return todos[:cantidad]

def crear_link_publico():
    return uuid.uuid4().hex[:12]

# minutos que dura una reserva sin pagar
RESERVA_MINUTOS = int(os.getenv("RESERVA_MINUTOS", "30"))

def liberar_reservas_expiradas(rifa_id: int):
    """Pone 'disponible' todo número 'reservado' cuyo reservado_hasta ya pasó."""
    con = db()
    cur = con.cursor()
    ahora = datetime.now()
    cur.execute("""
        UPDATE numeros
           SET estado='disponible', reservado_hasta=NULL, id_comprador=NULL
         WHERE id_rifa=%s
           AND estado='reservado'
           AND reservado_hasta IS NOT NULL
           AND reservado_hasta < %s
    """, (rifa_id, ahora))
    con.commit()
    con.close()

# ================== SUPERADMIN HELPER (nuevo, mínimo) ==================
def is_superadmin():
    """
    Permite entrar con ?token=... la primera vez y guarda la sesión.
    Luego, mientras dure la sesión, no pide el token otra vez.
    """
    tok = (request.args.get("token") or "").strip()
    if tok == SUPERADMIN_TOKEN:
        session["is_superadmin"] = True
    return session.get("is_superadmin") is True

# ================ RUTAS BASE ========================

# Usa el mismo token que ya tienes definido
SUPERADMIN_TOKEN = os.getenv("SUPERADMIN_TOKEN", "geica-dev")

def is_superadmin():
    """
    Marca sesión de superadmin si viene ?token=...
    y luego usa la marca de sesión en las siguientes visitas.
    """
    t = (request.args.get("token") or "").strip()
    if t:  # si me pasan el token, actualizo la sesión
        session['is_superadmin'] = (t == SUPERADMIN_TOKEN)
    return bool(session.get("is_superadmin"))

@app.get("/superadmin/logout")
def superadmin_logout():
    session.pop("is_superadmin", None)
    flash("Modo superadmin desactivado.", "info")
    return redirect(url_for("login"))

@app.post("/superadmin/crear-negocio")
def superadmin_crear_negocio():
    if not is_superadmin():
        flash("Token inválido o faltante.", "danger")
        return redirect(url_for("login"))

    nombre       = (request.form.get("nombre_negocio") or "").strip()
    propietario  = (request.form.get("nombre_propietario") or "").strip()
    correo       = (request.form.get("correo") or "").strip().lower()
    contrasena   = (request.form.get("contrasena") or "").strip()
    celular      = (request.form.get("celular") or "").strip()
    estado       = (request.form.get("estado") or "activo").strip().lower()
    pub          = (request.form.get("public_key_wompi") or "").strip()
    prv          = (request.form.get("private_key_wompi") or "").strip()
    itg          = (request.form.get("integrity_secret_wompi") or "").strip()
    chk          = (request.form.get("checkout_url_wompi") or "").strip()

    if not nombre or not correo or not contrasena:
        flash("Nombre, correo y contraseña son obligatorios.", "warning")
        return redirect(url_for("superadmin_panel"))

    # Aceptar llaves de producción O llaves de prueba
    if not (
        (pub.startswith("pub_prod_") and prv.startswith("prv_prod_") and itg.startswith("prod_integrity_")) or
        (pub.startswith("pub_test_") and prv.startswith("prv_test_") and itg.startswith("test_integrity_"))
    ):
        flash("Debes registrar llaves Wompi válidas (producción o prueba).", "danger")
        return redirect(url_for("superadmin_panel"))

    try:
        con = db()
        cur = con.cursor()  # para INSERT/SELECT no necesitamos dict aquí

        # Duplicado por correo
        cur.execute("SELECT 1 FROM negocios WHERE correo = %s", (correo,))
        if cur.fetchone():
            con.close()
            flash("Ese correo ya está registrado.", "danger")
            return redirect(url_for("superadmin_panel"))

        cur.execute("""
            INSERT INTO negocios
                (nombre_negocio, nombre_propietario, celular, correo, contrasena,
                 public_key_wompi, private_key_wompi, integrity_secret_wompi,
                 checkout_url_wompi, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (nombre, propietario or nombre, celular, correo, contrasena,
              pub, prv, itg, chk, estado))
        con.commit()
        con.close()
        flash("Negocio creado ✅ (llaves de producción registradas)", "success")
    except Exception as e:
        try:
            con.rollback()
            con.close()
        except Exception:
            pass
        flash(f"Error creando negocio: {e}", "danger")

    return redirect(url_for("superadmin_panel"))

@app.route("/superadmin")
def superadmin_panel():
    if not is_superadmin():
        flash("Acceso denegado. Entra con ?token=geica-dev la primera vez.", "danger")
        return redirect(url_for("login"))

    try:
        con = db()
        cur = con.cursor(cursor_factory=RealDictCursor)  # <-- para que el template use n["id"]
        cur.execute("""
            SELECT 
              n.id,
              n.nombre_negocio,
              n.correo,
              n.celular,
              n.estado,
              (SELECT COUNT(1) FROM rifas r WHERE r.id_negocio = n.id) AS total_rifas
            FROM negocios n
            ORDER BY n.id DESC
        """)
        negocios = cur.fetchall()
        con.close()
        return render_template("panel_superadmin.html", negocios=negocios)
    except Exception as e:
        try:
            con.close()
        except Exception:
            pass
        # Si algo truena (p. ej., tabla no existe), no muestres 500 en blanco
        flash(f"Error cargando panel de superadmin: {e}", "danger")
        return redirect(url_for("login"))

@app.post("/superadmin/estado-negocio")
def superadmin_estado_negocio():
    if not is_superadmin():
        flash("Token inválido o faltante.", "danger")
        return redirect(url_for("login"))

    negocio_id = request.form.get("negocio_id")
    accion = (request.form.get("accion") or "").strip()

    if not negocio_id or accion not in ("activar", "desactivar"):
        flash("Solicitud inválida.", "warning")
        return redirect(url_for("superadmin_panel"))

    nuevo_estado = "activo" if accion == "activar" else "inactivo"

    try:
        con = db()
        cur = con.cursor()
        cur.execute("UPDATE negocios SET estado=%s WHERE id=%s", (nuevo_estado, negocio_id))
        con.commit()
        con.close()
        flash(f"Negocio {accion}do correctamente.", "success")
    except Exception as e:
        try:
            con.rollback(); con.close()
        except Exception:
            pass
        flash(f"No se pudo actualizar el estado: {e}", "danger")

    return redirect(url_for("superadmin_panel"))

@app.route("/")
def home():
    if session.get("negocio_id"):
        return redirect(url_for("panel"))
    return redirect(url_for("login"))

# ---------------- LOGIN / LOGOUT --------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        correo = (request.form.get("correo") or "").strip()
        contrasena = (request.form.get("clave") or request.form.get("contrasena") or "").strip()
        con = db()
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM negocios WHERE correo = %s AND contrasena = %s AND estado = 'activo'",
            (correo, contrasena)
        )
        row = cur.fetchone()
        con.close()
        if row:
            session["negocio_id"] = row["id"]
            session["negocio_nombre"] = row["nombre_negocio"]
            flash("¡Bienvenido! ✅", "success")
            return redirect(url_for("panel"))
        flash("Credenciales inválidas o negocio inactivo.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("login"))

# ---------------- CREAR RIFA ------------------------
@app.route("/crear-rifa", methods=["GET", "POST"])
def crear_rifa():
    negocio = negocio_actual()
    if not negocio:
        return redirect(url_for("login"))

    if request.method == "POST":
        nombre      = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        avaluo      = request.form.get("avaluo", "").strip()
        cifras      = int(request.form.get("cifras", "2"))
        cantidad    = int(request.form.get("cantidad_numeros", "100"))
        valor       = int(request.form.get("valor_numero", "0"))
        loteria     = request.form.get("nombre_loteria", "").strip()

        # Imagen
        imagen_premio = None
        file = request.files.get("imagen_premio")
        if file and file.filename and allowed_file(file.filename):
            fname   = secure_filename(file.filename)
            unique  = f"{uuid.uuid4().hex}_{fname}"
            save_as = os.path.join(UPLOAD_DIR, unique)
            file.save(save_as)
            imagen_premio = f"uploads/{unique}"

        # Reglas de generación
        if cifras == 2:
            cantidad = 100  # fijo

        link_publico = crear_link_publico()

        con = db()
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO rifas (id_negocio, nombre, descripcion, avaluo, cifras, cantidad_numeros,
                               valor_numero, nombre_loteria, imagen_premio, link_publico, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'activa')
            RETURNING id
        """, (
            negocio["id"], nombre, descripcion, avaluo, cifras, cantidad,
            valor, loteria, imagen_premio, link_publico
        ))
        rifa_id = cur.fetchone()["id"]

        # Generar talonario
        lista = generar_numeros(cifras, cantidad)
        cur.executemany(
            "INSERT INTO numeros (id_rifa, numero, estado) VALUES (%s, %s, 'disponible')",
            [(rifa_id, n) for n in lista]
        )
        con.commit()
        con.close()

        flash("Rifa creada con éxito. ¡Comparte tu link!", "success")
        return redirect(url_for("ver_rifas"))

    return render_template("crear_rifa.html", negocio=negocio)

# ---------------- VER RIFAS -------------------------
@app.route("/ver-rifas")
def ver_rifas():
    negocio = negocio_actual()
    if not negocio:
        return redirect(url_for("login"))

    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT r.*, 
               (SELECT COUNT(*) FROM numeros n WHERE n.id_rifa = r.id AND n.estado='pagado') AS vendidos
        FROM rifas r
        WHERE r.id_negocio = %s
        ORDER BY r.id DESC
    """, (negocio["id"],))
    rifas = cur.fetchall()
    con.close()
    return render_template("ver_rifas.html", negocio=negocio, rifas=rifas)

# --------------- VISTA PÚBLICA RIFA -----------------
@app.route("/r/<link_publico>", methods=["GET"])
def rifa_publica(link_publico):
    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM rifas WHERE link_publico = %s AND estado='activa'", (link_publico,))
    rifa = cur.fetchone()
    if not rifa:
        con.close()
        abort(404)
    con.close()

    # limpiar reservas vencidas antes de mostrar
    liberar_reservas_expiradas(rifa["id"])

    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM negocios WHERE id = %s", (rifa["id_negocio"],))
    negocio = cur.fetchone()

    cur.execute("""
        SELECT id, numero, estado FROM numeros
        WHERE id_rifa = %s
        ORDER BY numero ASC
    """, (rifa["id"],))
    numeros = cur.fetchall()
    con.close()

    numeros_fmt = [
        {"id": row["id"], "numero": row["numero"], "estado": numero_estado_css(row["estado"])}
        for row in numeros
    ]
    return render_template("rifa_publica.html", rifa=rifa, negocio=negocio, numeros=numeros_fmt)

# ------- GENERAR PAGO (SELECCIÓN + DATOS CLIENTE) ---
@app.route("/generar-pago", methods=["POST"])
def generar_pago():
    """
    1) Valida datos y rifa activa
    2) Limpia reservas vencidas
    3) Verifica disponibilidad y RESERVA con expiración
    4) Crea/actualiza comprador y compra 'pendiente'
    5) Genera link Wompi (ahora acepta PRODUCCIÓN o PRUEBA)
    """
    data = request.form
    try:
        rifa_id = int(data.get("rifa_id"))
    except Exception:
        return jsonify({"ok": False, "error": "Rifa inválida"}), 400

    numeros_req = [x.strip() for x in (data.get("numeros", "")).split(",") if x.strip()]
    nombre   = (data.get("nombre") or "").strip()
    cedula   = (data.get("cedula") or "").strip()
    correo   = (data.get("correo") or "").strip()
    telefono = (data.get("telefono") or "").strip()

    if not (rifa_id and numeros_req and nombre and cedula and correo and telefono):
        return jsonify({"ok": False, "error": "Datos incompletos"}), 400

    con = db(); cur = con.cursor(cursor_factory=RealDictCursor)

    # 1) Rifa activa
    cur.execute("SELECT * FROM rifas WHERE id = %s AND estado='activa'", (rifa_id,))
    rifa = cur.fetchone()
    if not rifa:
        con.close()
        return jsonify({"ok": False, "error": "Rifa no disponible"}), 400

    # 2) Limpia reservas vencidas
    con.commit(); con.close()
    liberar_reservas_expiradas(rifa_id)

    con = db(); cur = con.cursor(cursor_factory=RealDictCursor)

    # Carga negocio
    cur.execute("SELECT * FROM negocios WHERE id = %s", (rifa["id_negocio"],))
    negocio = cur.fetchone()

    # 3) Validar disponibilidad
    qmarks = ",".join(["%s"] * len(numeros_req))
    cur.execute(f"""
        SELECT id, numero, estado FROM numeros
        WHERE id_rifa = %s AND numero IN ({qmarks})
    """, (rifa_id, *numeros_req))
    filas = cur.fetchall()

    if len(filas) != len(numeros_req) or any(row["estado"] != "disponible" for row in filas):
        con.close()
        return jsonify({"ok": False, "error": "Alguno de los números ya no está disponible"}), 409

    # 3b) Reservar con expiración
    limite = datetime.now() + timedelta(minutes=RESERVA_MINUTOS)
    ids_numeros = [row["id"] for row in filas]
    cur.executemany(
        "UPDATE numeros SET estado='reservado', reservado_hasta=%s WHERE id = %s",
        [(limite, i) for i in ids_numeros]
    )

    # 4) Crear/actualizar comprador
    cur.execute("SELECT id FROM compradores WHERE cedula = %s", (cedula,))
    cmp = cur.fetchone()
    if cmp:
        comprador_id = cmp["id"] if isinstance(cmp, dict) else cmp[0]
        cur.execute(
            "UPDATE compradores SET nombre=%s, correo=%s, telefono=%s WHERE id=%s",
            (nombre, correo, telefono, comprador_id)
        )
    else:
        cur.execute(
            "INSERT INTO compradores (nombre, cedula, correo, telefono) VALUES (%s, %s, %s, %s) RETURNING id",
            (nombre, cedula, correo, telefono)
        )
        comprador_id = cur.fetchone()["id"]

    # 4b) Crear compra pendiente
    total = int(rifa["valor_numero"]) * len(numeros_req)
    numeros_str = ",".join(numeros_req)
    cur.execute("""
        INSERT INTO compras (id_comprador, id_rifa, numeros, total, fecha, estado)
        VALUES (%s, %s, %s, %s, %s, 'pendiente')
        RETURNING id
    """, (comprador_id, rifa_id, numeros_str, total, datetime.now()))
    compra_id = cur.fetchone()["id"]
    con.commit()

    # 5) Link de pago (ACEPTA PRODUCCIÓN o PRUEBA)
    def _clean(s): return (s or "").strip()
    pub = _clean(negocio.get("public_key_wompi", ""))
    prv = _clean(negocio.get("private_key_wompi", ""))
    itg = _clean(negocio.get("integrity_secret_wompi", ""))
    chk = _clean(negocio.get("checkout_url_wompi", ""))

    is_prod = pub.startswith("pub_prod_") and prv.startswith("prv_prod_") and itg.startswith("prod_integrity_")
    is_test = pub.startswith("pub_test_") and prv.startswith("prv_test_") and itg.startswith("test_integrity_")

    if is_prod:
        wompi_env = "production"
    elif is_test:
        wompi_env = "sandbox"   # ambiente de pruebas
    else:
        # liberar reservas y eliminar compra si las llaves no matchean ningún esquema
        qin = ",".join(["%s"] * len(ids_numeros))
        cur.execute(f"UPDATE numeros SET estado='disponible', reservado_hasta=NULL WHERE id IN ({qin})", ids_numeros)
        cur.execute("DELETE FROM compras WHERE id = %s", (compra_id,))
        con.commit(); con.close()
        return jsonify({"ok": False, "error": "Llaves Wompi inválidas. Usa pub_prod_/prv_prod_/prod_integrity_ o pub_test_/prv_test_/test_integrity_."}), 400

    referencia  = f"compra_{compra_id}"
    descripcion = f"Rifa {rifa['nombre']} - Números {numeros_str}"

    try:
        checkout_url = generar_link_de_pago(
            amount_in_cents=total * 100,
            currency="COP",
            reference=referencia,
            description=descripcion,
            customer_email=correo,
            wompi_public_key=pub,
            wompi_private_key=prv,
            wompi_env=wompi_env,                          # <- producción o sandbox
            wompi_integrity_secret=itg,
            wompi_checkout_base=chk or "https://checkout.wompi.co/p/"  # base por defecto
        )
        con.close()
        return jsonify({"ok": True, "checkout_url": checkout_url})
    except Exception as e:
        # liberar reservas y borrar compra si algo falla
        qin = ",".join(["%s"] * len(ids_numeros))
        cur.execute(f"UPDATE numeros SET estado='disponible', reservado_hasta=NULL WHERE id IN ({qin})", ids_numeros)
        cur.execute("DELETE FROM compras WHERE id = %s", (compra_id,))
        con.commit(); con.close()
        return jsonify({"ok": False, "error": f"No se pudo generar el link de pago: {e}"}), 500
    
@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# ---------------- WEBHOOK PAGO ----------------------
@app.route("/webhook-pago", methods=["POST"])
def webhook_pago():
    """
    Wompi envía un evento -> verificamos y cerramos compra:
    - Si 'APPROVED' -> compra=pagado, números=pagado, reservado_hasta=NULL
    - Si 'DECLINED' / 'VOIDED' / 'ERROR' -> números=disponible, id_comprador=NULL, reservado_hasta=NULL
    """
    evento = request.get_json(silent=True) or {}
    result = verificar_evento_webhook(evento)
    if not result["ok"]:
        return jsonify({"ok": False}), 400

    reference = result["reference"]  # compra_x
    estado_tx = result["status"]     # APPROVED / DECLINED / VOIDED / ERROR

    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)

    # Solo procesamos referencias del tipo compra_#
    if not reference.startswith("compra_"):
        con.close()
        return jsonify({"ok": True}), 200

    compra_id = int(reference.split("_")[1])

    cur.execute("""
        SELECT c.*, r.id_negocio, r.nombre, r.valor_numero, r.id AS rifa_id
        FROM compras c
        JOIN rifas r ON r.id = c.id_rifa
        WHERE c.id = %s
    """, (compra_id,))
    compra = cur.fetchone()
    if not compra:
        con.close()
        return jsonify({"ok": True}), 200

    # Cargar negocio y comprador (para notificaciones)
    cur.execute("SELECT * FROM negocios WHERE id = %s", (compra["id_negocio"],))
    negocio = cur.fetchone()
    cur.execute("SELECT * FROM compradores WHERE id = %s", (compra["id_comprador"],))
    comprador = cur.fetchone()

    numeros_lista = [x.strip() for x in (compra["numeros"] or "").split(",") if x.strip()]

    if estado_tx == "APPROVED":
        # 1) marcar compra como pagada
        cur.execute("UPDATE compras SET estado='pagado' WHERE id = %s", (compra_id,))
        # 2) bloquear números
        if numeros_lista:
            qmarks = ",".join(["%s"] * len(numeros_lista))
            cur.execute(f"""
                UPDATE numeros
                   SET estado='pagado', id_comprador=%s, reservado_hasta=NULL
                 WHERE id_rifa=%s AND numero IN ({qmarks})
            """, (compra["id_comprador"], compra["rifa_id"], *numeros_lista))

        # 3) notificar
        try:
            mensaje = (f"🎉 ¡Pago aprobado!\nRifa: {compra['nombre']}\n"
                       f"Números: {compra['numeros']}\nTotal: ${compra['total']}\n¡Suerte!")
            if comprador and comprador.get("telefono"):
                enviar_whatsapp(comprador["telefono"], mensaje)
            if comprador and comprador.get("correo"):
                enviar_correo(comprador["correo"], "Compra confirmada", mensaje)

            admin_msg = (f"✅ Pago recibido\nCliente: {comprador['nombre']}\n"
                         f"Números: {compra['numeros']}\nTotal: ${compra['total']}")
            if negocio and negocio.get("celular"):
                enviar_whatsapp(negocio["celular"], admin_msg)
            if negocio and negocio.get("correo"):
                enviar_correo(negocio["correo"], "Nueva compra confirmada", admin_msg)
        except Exception as e:
            print("Error enviando notificaciones:", e)

    else:
        # Rechazado/anulado -> liberar números y dejar compra como pendiente
        if numeros_lista:
            qmarks = ",".join(["%s"] * len(numeros_lista))
            cur.execute(f"""
                UPDATE numeros
                   SET estado='disponible', id_comprador=NULL, reservado_hasta=NULL
                 WHERE id_rifa=%s AND numero IN ({qmarks})
            """, (compra["rifa_id"], *numeros_lista))
        cur.execute("UPDATE compras SET estado='pendiente' WHERE id = %s", (compra_id,))

    con.commit()
    con.close()
    return jsonify({"ok": True}), 200

# --------------- NOTIFICAR GANADOR ------------------
@app.route("/notificar-ganador", methods=["GET", "POST"])
def notificar_ganador():
    negocio = negocio_actual()
    if not negocio:
        return redirect(url_for("login"))

    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)

    if request.method == "POST":
        nombre_rifa    = (request.form.get("nombre_rifa") or "").strip()
        numero_ganador = (request.form.get("numero_ganador") or "").strip()

        # 1) Buscar rifa por nombre dentro del negocio
        cur.execute("SELECT * FROM rifas WHERE id_negocio=%s AND nombre=%s",
                    (negocio["id"], nombre_rifa))
        rifa = cur.fetchone()
        if not rifa:
            con.close()
            flash("Rifa no encontrada.", "warning")
            return redirect(url_for("notificar_ganador"))

        # 2) Normalizar número con ceros a la izquierda según las cifras de la rifa
        try:
            cifras = int(rifa["cifras"])
        except Exception:
            cifras = 2  # fallback
        numero_norm = numero_ganador.zfill(cifras)

        # 3) Consultar el número (con datos del comprador)
        cur.execute("""
            SELECT n.*, c.nombre AS comprador_nombre, c.correo AS comprador_correo,
                   c.telefono AS comprador_tel
              FROM numeros n
         LEFT JOIN compradores c ON c.id = n.id_comprador
             WHERE n.id_rifa=%s AND n.numero=%s
        """, (rifa["id"], numero_norm))
        fila = cur.fetchone()
        con.close()

        if not fila or fila["estado"] != "pagado":
            flash("El número no corresponde a un comprador pagado.", "danger")
            return redirect(url_for("notificar_ganador"))

        # 4) Notificar ganador
        msg_txt = (f"🎉 ¡Felicidades! Eres el ganador de la rifa '{nombre_rifa}' "
                   f"con el número {numero_norm}. Pronto te contactarán.")
        try:
            if fila.get("comprador_tel"):
                enviar_whatsapp(fila["comprador_tel"], msg_txt)
            if fila.get("comprador_correo"):
                enviar_correo(fila["comprador_correo"], "¡Eres el ganador!", msg_txt)
            flash("Ganador notificado con éxito.", "success")
        except Exception as e:
            print("Error notificando ganador:", e)
            flash("No se pudo notificar al ganador.", "danger")

        return redirect(url_for("panel"))

    # GET -> lista de nombres de rifas del negocio para el select
    cur.execute("SELECT nombre FROM rifas WHERE id_negocio=%s ORDER BY id DESC", (negocio["id"],))
    nombres = [r["nombre"] for r in cur.fetchall()]
    con.close()
    return render_template("notificar_ganador.html", negocio=negocio, nombres_rifas=nombres)

# --------------- ARCHIVOS SUBIDOS -------------------
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ================== MANEJO DE ERRORES ===============
@app.errorhandler(404)
def not_found(e):
    return "Recurso no encontrado.", 404

@app.errorhandler(500)
def server_error(e):
    return "Error interno del servidor.", 500

@app.route('/actualizar-rifas')
def actualizar_rifas():
    if 'negocio_id' not in session:
        return redirect(url_for('login'))

    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, nombre, descripcion, valor_numero, cifras, cantidad_numeros, estado
          FROM rifas
         WHERE id_negocio = %s AND estado = 'activa'
         ORDER BY id DESC
    """, (session['negocio_id'],))
    rifas_activas = cur.fetchall()
    con.close()

    return render_template('actualizar_rifas.html', rifas=rifas_activas)

SOPORTE_URL = "https://wa.me/573105494296?text=Hola%20necesito%20soporte%20GEICACONTROLRIFAS"

@app.context_processor
def inject_soporte():
    return {"soporte_url": SOPORTE_URL}

@app.get("/soporte")
def soporte():
    from flask import redirect
    return redirect(SOPORTE_URL)

def rifas_resumen_por_negocio(negocio_id: int):
    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            r.id, r.nombre, r.descripcion, r.valor_numero, r.cifras, r.cantidad_numeros,
            r.estado, r.link_publico,
            (SELECT COUNT(*) FROM numeros n WHERE n.id_rifa = r.id) AS total,
            (SELECT COUNT(*) FROM numeros n WHERE n.id_rifa = r.id AND n.estado='pagado') AS vendidos
          FROM rifas r
         WHERE r.id_negocio = %s
         ORDER BY r.id DESC
    """, (negocio_id,))
    rows = cur.fetchall()
    con.close()
    return rows

@app.route("/panel")
def panel():
    negocio = negocio_actual()
    if not negocio:
        return redirect(url_for("login"))
    rifas = rifas_resumen_por_negocio(negocio["id"])  # <- listo para las cards
    return render_template("panel_admin.html", negocio=negocio, rifas=rifas)

@app.get("/gracias")
def gracias():
    # Wompi solo redirige aquí para mostrar algo al usuario.
    # La confirmación REAL la hace el webhook.
    return """
    <div style="font-family:system-ui;max-width:600px;margin:40px auto;text-align:center">
      <h2>✅ Pago en proceso</h2>
      <p>Estamos confirmando tu pago con Wompi.</p>
      <p>Puedes cerrar esta ventana. Te avisaremos por WhatsApp/Correo.</p>
      <a href="/" style="display:inline-block;margin-top:16px">Ir al panel</a>
    </div>
    """

# ================== MAIN ============================
if __name__ == "__main__":
    print(">>> Arrancando servidor Flask en puerto 10000...")
    app.run(host="0.0.0.0", debug=True, port=10000)