import os
import uuid
import random
import sqlite3
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, session, send_from_directory, jsonify, abort)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Módulos internos
from notificaciones.notificaciones import enviar_whatsapp, enviar_correo
from pagos.wompi import generar_link_de_pago, verificar_evento_webhook

# 🔑 Token de acceso para Superadmin
SUPERADMIN_TOKEN = "geica-dev"  # cámbialo si quieres otro valor

# ================== CONFIG INICIAL ==================
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH  = os.path.join(BASE_DIR, "geicacontrolrifas.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "geica-super-secret")

# Fallback (por si un negocio no tiene sus propias llaves)
ENV_WOMPI_PUBLIC  = os.getenv("WOMPI_PUBLIC_KEY", "")
ENV_WOMPI_PRIVATE = os.getenv("WOMPI_PRIVATE_KEY", "")
ENV_WOMPI_ENV     = os.getenv("WOMPI_ENV", "sandbox")

# ================ HELPERS DE DB =====================
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def negocio_actual():
    """Retorna el registro del negocio logueado o None."""
    nid = session.get("negocio_id")
    if not nid:
        return None
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM negocios WHERE id = ?", (nid,))
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
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        UPDATE numeros
        SET estado='disponible', reservado_hasta=NULL, id_comprador=NULL
        WHERE id_rifa=? AND estado='reservado' AND reservado_hasta IS NOT NULL AND reservado_hasta < ?
    """, (rifa_id, ahora))
    con.commit()
    con.close()

# ================ RUTAS BASE ========================

@app.post("/superadmin/crear-negocio")
def superadmin_crear_negocio():
    token = request.args.get("token", "")
    if token != SUPERADMIN_TOKEN:
        flash("Token inválido", "danger")
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
        return redirect(url_for("superadmin_panel", token=token))

    # Validar PRODUCCIÓN obligatoria
    if not (pub.startswith("pub_prod_") and prv.startswith("prv_prod_") and itg.startswith("prod_integrity_")):
        flash("Debes registrar llaves Wompi de PRODUCCIÓN (pub_prod_ / prv_prod_ / prod_integrity_).", "danger")
        return redirect(url_for("superadmin_panel", token=token))

    con = db(); cur = con.cursor()

    # Evita duplicados por correo
    cur.execute("SELECT 1 FROM negocios WHERE correo = ?", (correo,))
    if cur.fetchone():
        con.close()
        flash("Ese correo ya está registrado.", "danger")
        return redirect(url_for("superadmin_panel", token=token))

    # Inserta negocio
    cur.execute("""
        INSERT INTO negocios (nombre_negocio, nombre_propietario, celular, correo, contrasena,
                              public_key_wompi, private_key_wompi, integrity_secret_wompi, checkout_url_wompi, estado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (nombre, propietario or nombre, celular, correo, contrasena,
          pub, prv, itg, chk, estado))
    con.commit(); con.close()

    flash("Negocio creado ✅ (llaves de producción registradas)", "success")
    return redirect(url_for("superadmin_panel", token=token))


@app.route("/superadmin")
def superadmin_panel():
    token = request.args.get("token", "")
    if token != SUPERADMIN_TOKEN:
        flash("Acceso denegado. Agrega ?token=geica-dev", "danger")
        return redirect(url_for("login"))

    con = db()
    cur = con.cursor()
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

    return render_template("panel_superadmin.html", negocios=negocios, token=token)


@app.post("/superadmin/estado-negocio")
def superadmin_estado_negocio():
    token = request.args.get("token", "")
    if token != SUPERADMIN_TOKEN:
        flash("Token inválido", "danger")
        return redirect(url_for("login"))

    negocio_id = request.form.get("negocio_id")
    accion = (request.form.get("accion") or "").strip()

    if not negocio_id or accion not in ("activar", "desactivar"):
        flash("Solicitud inválida", "warning")
        return redirect(url_for("superadmin_panel", token=token))

    nuevo_estado = "activo" if accion == "activar" else "inactivo"

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE negocios SET estado=? WHERE id=?", (nuevo_estado, negocio_id))
    con.commit()
    con.close()

    flash(f"Negocio {accion}do correctamente.", "success")
    return redirect(url_for("superadmin_panel", token=token))

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
        cur = con.cursor()
        cur.execute(
            "SELECT * FROM negocios WHERE correo = ? AND contrasena = ? AND estado = 'activo'",
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
        nombre   = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        avaluo   = request.form.get("avaluo", "").strip()
        cifras   = int(request.form.get("cifras", "2"))
        cantidad = int(request.form.get("cantidad_numeros", "100"))
        valor    = int(request.form.get("valor_numero", "0"))
        loteria  = request.form.get("nombre_loteria", "").strip()

        # Imagen
        imagen_premio = None
        file = request.files.get("imagen_premio")
        if file and file.filename and allowed_file(file.filename):
            fname = secure_filename(file.filename)
            unique = f"{uuid.uuid4().hex}_{fname}"
            save_path = os.path.join(UPLOAD_DIR, unique)
            file.save(save_path)
            imagen_premio = f"uploads/{unique}"

        # Reglas de generación
        if cifras == 2:
            cantidad = 100  # fijo

        link_publico = crear_link_publico()

        con = db()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO rifas (id_negocio, nombre, descripcion, avaluo, cifras, cantidad_numeros,
                               valor_numero, nombre_loteria, imagen_premio, link_publico, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activa')
        """, (
            negocio["id"], nombre, descripcion, avaluo, cifras, cantidad,
            valor, loteria, imagen_premio, link_publico
        ))
        rifa_id = cur.lastrowid

        # Generar talonario
        lista = generar_numeros(cifras, cantidad)
        cur.executemany(
            "INSERT INTO numeros (id_rifa, numero, estado) VALUES (?, ?, 'disponible')",
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
    cur = con.cursor()
    cur.execute("""
        SELECT r.*, 
               (SELECT COUNT(*) FROM numeros n WHERE n.id_rifa = r.id AND n.estado='pagado') AS vendidos
        FROM rifas r
        WHERE r.id_negocio = ?
        ORDER BY r.id DESC
    """, (negocio["id"],))
    rifas = cur.fetchall()
    con.close()
    return render_template("ver_rifas.html", negocio=negocio, rifas=rifas)

# --------------- VISTA PÚBLICA RIFA -----------------

@app.route("/r/<link_publico>", methods=["GET"])
def rifa_publica(link_publico):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM rifas WHERE link_publico = ? AND estado='activa'", (link_publico,))
    rifa = cur.fetchone()
    if not rifa:
        con.close()
        abort(404)

    # 🔸 limpiar reservas vencidas antes de mostrar el talonario
    con.close()
    liberar_reservas_expiradas(rifa["id"])

    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM negocios WHERE id = ?", (rifa["id_negocio"],))
    negocio = cur.fetchone()

    cur.execute("""
        SELECT id, numero, estado FROM numeros
        WHERE id_rifa = ?
        ORDER BY numero ASC
    """, (rifa["id"],))
    numeros = cur.fetchall()
    con.close()

    ...

    # el template usará clases CSS por estado
    numeros_fmt = [
        {
            "id": row["id"],
            "numero": row["numero"],
            "estado": numero_estado_css(row["estado"])
        } for row in numeros
    ]
    return render_template("rifa_publica.html", rifa=rifa, negocio=negocio, numeros=numeros_fmt)

# ------- GENERAR PAGO (SELECCIÓN + DATOS CLIENTE) ---
@app.route("/generar-pago", methods=["POST"])
def generar_pago():
    """
    Flujo:
      1) Valida datos y rifa activa
      2) Limpia reservas vencidas
      3) Verifica disponibilidad y RESERVA con expiración
      4) Crea/actualiza comprador y compra 'pendiente'
      5) Genera link Wompi (PRODUCCIÓN obligatoria)
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

    con = db(); cur = con.cursor()

    # 1) Rifa activa
    cur.execute("SELECT * FROM rifas WHERE id = ? AND estado='activa'", (rifa_id,))
    rifa = cur.fetchone()
    if not rifa:
        con.close()
        return jsonify({"ok": False, "error": "Rifa no disponible"}), 400

    # 2) Limpia reservas vencidas
    con.commit(); con.close()
    liberar_reservas_expiradas(rifa_id)

    con = db(); cur = con.cursor()

    # Carga negocio (DEBE TENER LLAVES DE PRODUCCIÓN)
    cur.execute("SELECT * FROM negocios WHERE id = ?", (rifa["id_negocio"],))
    negocio = cur.fetchone()

    # 3) Validar disponibilidad
    qmarks = ",".join("?" for _ in numeros_req)
    cur.execute(f"""
        SELECT id, numero, estado FROM numeros
        WHERE id_rifa = ? AND numero IN ({qmarks})
    """, (rifa_id, *numeros_req))
    filas = cur.fetchall()

    if len(filas) != len(numeros_req) or any(row["estado"] != "disponible" for row in filas):
        con.close()
        return jsonify({"ok": False, "error": "Alguno de los números ya no está disponible"}), 409

    # 3b) Reservar con expiración
    limite = (datetime.now() + timedelta(minutes=RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
    ids_numeros = [row["id"] for row in filas]
    cur.executemany(
        "UPDATE numeros SET estado='reservado', reservado_hasta=? WHERE id = ?",
        [(limite, i) for i in ids_numeros]
    )

    # 4) Crear/actualizar comprador
    cur.execute("SELECT id FROM compradores WHERE cedula = ?", (cedula,))
    cmp = cur.fetchone()
    if cmp:
        comprador_id = cmp["id"]
        cur.execute(
            "UPDATE compradores SET nombre=?, correo=?, telefono=? WHERE id=?",
            (nombre, correo, telefono, comprador_id)
        )
    else:
        cur.execute(
            "INSERT INTO compradores (nombre, cedula, correo, telefono) VALUES (?, ?, ?, ?)",
            (nombre, cedula, correo, telefono)
        )
        comprador_id = cur.lastrowid

    # 4b) Crear compra pendiente
    total = int(rifa["valor_numero"]) * len(numeros_req)
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    numeros_str = ",".join(numeros_req)

    cur.execute("""
        INSERT INTO compras (id_comprador, id_rifa, numeros, total, fecha, estado)
        VALUES (?, ?, ?, ?, ?, 'pendiente')
    """, (comprador_id, rifa_id, numeros_str, total, fecha))
    compra_id = cur.lastrowid
    con.commit()

    # 5) Link de pago (PRODUCCIÓN OBLIGATORIA)
    def _clean(s): return (s or "").strip()
    pub = _clean(negocio["public_key_wompi"]) if "public_key_wompi" in negocio.keys() else ""
    prv = _clean(negocio["private_key_wompi"]) if "private_key_wompi" in negocio.keys() else ""
    itg = _clean(negocio["integrity_secret_wompi"]) if "integrity_secret_wompi" in negocio.keys() else ""
    chk = _clean(negocio["checkout_url_wompi"])     if "checkout_url_wompi"     in negocio.keys() else ""

    # Log minimal
    print("[WOMPI][APP][PROD]", "pub=", pub[:12], "itg=", (itg or "")[:16], "total=", total, flush=True)

    # Validación estricta de producción
    if not (pub.startswith("pub_prod_") and prv.startswith("prv_prod_") and itg.startswith("prod_integrity_")):
        # liberar reservas y eliminar compra para no “pegar” números
        placeholders = ",".join("?" for _ in ids_numeros)
        cur.execute(f"UPDATE numeros SET estado='disponible', reservado_hasta=NULL WHERE id IN ({placeholders})", ids_numeros)
        cur.execute("DELETE FROM compras WHERE id = ?", (compra_id,))
        con.commit(); con.close()
        return jsonify({"ok": False, "error": "Llaves Wompi inválidas. Se requieren pub_prod_ / prv_prod_ / prod_integrity_."}), 400

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
            wompi_env="production",              # fijo
            wompi_integrity_secret=itg,
            wompi_checkout_base=chk or "https://checkout.wompi.co/p/"
        )
        con.close()
        return jsonify({"ok": True, "checkout_url": checkout_url})
    except Exception as e:
        # liberar reservas y borrar compra si algo falla
        placeholders = ",".join("?" for _ in ids_numeros)
        cur.execute(f"UPDATE numeros SET estado='disponible', reservado_hasta=NULL WHERE id IN ({placeholders})", ids_numeros)
        cur.execute("DELETE FROM compras WHERE id = ?", (compra_id,))
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
    cur = con.cursor()

    # Solo procesamos referencias del tipo compra_#
    if not reference.startswith("compra_"):
        con.close()
        return jsonify({"ok": True}), 200

    compra_id = int(reference.split("_")[1])

    cur.execute("""
        SELECT c.*, r.id_negocio, r.nombre, r.valor_numero, r.id AS rifa_id
        FROM compras c
        JOIN rifas r ON r.id = c.id_rifa
        WHERE c.id = ?
    """, (compra_id,))
    compra = cur.fetchone()
    if not compra:
        con.close()
        return jsonify({"ok": True}), 200

    # Cargar negocio y comprador (para notificaciones)
    cur.execute("SELECT * FROM negocios WHERE id = ?", (compra["id_negocio"],))
    negocio = cur.fetchone()
    cur.execute("SELECT * FROM compradores WHERE id = ?", (compra["id_comprador"],))
    comprador = cur.fetchone()

    numeros_lista = [x.strip() for x in (compra["numeros"] or "").split(",") if x.strip()]
    qmarks = ",".join("?" for _ in numeros_lista) if numeros_lista else ""

    if estado_tx == "APPROVED":
        # 1) marcar compra como pagada
        cur.execute("UPDATE compras SET estado='pagado' WHERE id = ?", (compra_id,))
        # 2) bloquear números: pagado + limpiar reservado_hasta (si lo tienes en el schema)
        if numeros_lista:
            cur.execute(f"""
                UPDATE numeros
                SET estado='pagado', id_comprador=?, reservado_hasta=NULL
                WHERE id_rifa=? AND numero IN ({qmarks})
            """, (compra["id_comprador"], compra["rifa_id"], *numeros_lista))

        # 3) notificar (sin romper si faltan datos)
        try:
            mensaje = (f"🎉 ¡Pago aprobado!\nRifa: {compra['nombre']}\n"
                       f"Números: {compra['numeros']}\nTotal: ${compra['total']}\n¡Suerte!")
            if comprador and comprador["telefono"]:
                enviar_whatsapp(comprador["telefono"], mensaje)
            if comprador and comprador["correo"]:
                enviar_correo(comprador["correo"], "Compra confirmada", mensaje)

            admin_msg = (f"✅ Pago recibido\nCliente: {comprador['nombre']}\n"
                         f"Números: {compra['numeros']}\nTotal: ${compra['total']}")
            if negocio and negocio["celular"]:
                enviar_whatsapp(negocio["celular"], admin_msg)
            if negocio and negocio["correo"]:
                enviar_correo(negocio["correo"], "Nueva compra confirmada", admin_msg)
        except Exception as e:
            print("Error enviando notificaciones:", e)

    else:
        # Rechazado/anulado -> liberar números y dejar compra como pendiente
        if numeros_lista:
            cur.execute(f"""
                UPDATE numeros
                SET estado='disponible', id_comprador=NULL, reservado_hasta=NULL
                WHERE id_rifa=? AND numero IN ({qmarks})
            """, (compra["rifa_id"], *numeros_lista))
        cur.execute("UPDATE compras SET estado='pendiente' WHERE id = ?", (compra_id,))

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
    cur = con.cursor()

    if request.method == "POST":
        nombre_rifa = request.form.get("nombre_rifa", "").strip()
        numero_ganador = request.form.get("numero_ganador", "").strip()

        cur.execute("SELECT * FROM rifas WHERE id_negocio=? AND nombre=?",
                    (negocio["id"], nombre_rifa))
        rifa = cur.fetchone()
        if not rifa:
            con.close()
            flash("Rifa no encontrada.", "warning")
            return redirect(url_for("notificar_ganador"))

        cur.execute("""
            SELECT n.*, c.nombre AS comprador_nombre, c.correo AS comprador_correo,
                   c.telefono AS comprador_tel
            FROM numeros n
            LEFT JOIN compradores c ON c.id = n.id_comprador
            WHERE n.id_rifa=? AND n.numero=?
        """, (rifa["id"], numero_ganador))
        fila = cur.fetchone()
        con.close()

        if not fila or fila["estado"] != "pagado":
            flash("El número no corresponde a un comprador pagado.", "danger")
            return redirect(url_for("notificar_ganador"))

        # Notificar ganador
        mensaje = (f"🎉 ¡Felicidades! Eres el ganador de la rifa '{nombre_rifa}' "
                   f"con el número {numero_ganador}. Pronto te contactarán.")
        try:
            if fila["comprador_tel"]:
                enviar_whatsapp(fila["comprador_tel"], mensaje)
            if fila["comprador_correo"]:
                enviar_correo(fila["comprador_correo"], "¡Eres el ganador!", mensaje)
            flash("Ganador notificado con éxito.", "success")
        except Exception as e:
            print("Error notificando ganador:", e)
            flash("No se pudo notificar al ganador.", "danger")

        return redirect(url_for("panel"))

    # GET -> Cargar rifas del negocio para autocompletar
    cur.execute("SELECT nombre FROM rifas WHERE id_negocio=? ORDER BY id DESC", (negocio["id"],))
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

    con = db()  # usa DB_PATH y row_factory
    cur = con.cursor()
    cur.execute("""
        SELECT id, nombre, descripcion, valor_numero, cifras, cantidad_numeros, estado
        FROM rifas
        WHERE id_negocio = ? AND estado = 'activa'
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
    cur = con.cursor()
    cur.execute("""
        SELECT
            r.id, r.nombre, r.descripcion, r.valor_numero, r.cifras, r.cantidad_numeros,
            r.estado, r.link_publico,
            (SELECT COUNT(*) FROM numeros n WHERE n.id_rifa = r.id) AS total,
            (SELECT COUNT(*) FROM numeros n WHERE n.id_rifa = r.id AND n.estado='pagado') AS vendidos
        FROM rifas r
        WHERE r.id_negocio = ?
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
