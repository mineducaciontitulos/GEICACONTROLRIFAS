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
from flask import url_for
from twilio.twiml.messaging_response import MessagingResponse
import re

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

import re

from datetime import datetime

def archivar_rifas_vencidas():
    """
    Archiva toda rifa 'activa' cuya fecha_fin ya pasó.
    Si fecha_fin es NULL, no hace nada.
    Es idempotente y silencioso (no rompe requests).
    """
    try:
        con = db()
        cur = con.cursor()
        cur.execute("""
            UPDATE rifas
               SET estado = 'archivada'
             WHERE estado = 'activa'
               AND fecha_fin IS NOT NULL
               AND NOW() >= fecha_fin
        """)
        con.commit()
        con.close()
    except Exception:
        # no levantamos error para no afectar la UX
        pass


def _normalize_msisdn(s: str) -> str:
    """Deja solo dígitos del MSISDN. Ej: 'whatsapp:+57 310-123-4567' -> '573101234567'"""
    if not s: return ""
    s = s.strip().replace("whatsapp:", "")
    return re.sub(r"\D+", "", s)  # solo dígitos

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

def find_negocio_by_twilio_to(twilio_to: str):
    """Modo A: resolución por número receptor (To). Debe estar guardado tal cual en negocios.wa_numero_receptor"""
    if not twilio_to:
        return None
    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT * FROM negocios 
         WHERE wa_numero_receptor IS NOT NULL 
           AND TRIM(wa_numero_receptor) <> ''
           AND REPLACE(REPLACE(wa_numero_receptor,' ',''),'+','') = REPLACE(REPLACE(%s,' ',''),'+','')
        LIMIT 1
    """, (twilio_to,))
    row = cur.fetchone()
    con.close()
    return row

def find_negocio_by_hint(body_text: str):
    """
    Modo B: si todos usan el mismo número de Twilio, tratamos de deducir el negocio:
    - si el texto trae @negocio (ej: @MiTienda)
    - si trae un link público /r/<link_publico>
    - si menciona el nombre del negocio (búsqueda 'like')
    Retorna el primer match razonable o None.
    """
    if not body_text:
        return None
    txt = body_text.strip()

    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)

    # 1) @alias
    m = re.search(r'@([A-Za-z0-9_\-\.]{2,32})', txt)
    if m:
        alias = m.group(1)
        cur.execute("""
            SELECT * FROM negocios 
             WHERE LOWER(nombre_negocio) LIKE LOWER(%s) 
                OR LOWER(nombre_propietario) LIKE LOWER(%s)
            LIMIT 1
        """, (f"%{alias}%", f"%{alias}%"))
        row = cur.fetchone()
        if row:
            con.close()
            return row

    # 2) link público /r/<link>
    m = re.search(r'/r/([A-Za-z0-9]{6,})', txt)
    if m:
        link = m.group(1)
        cur.execute("""
            SELECT n.* FROM rifas r
            JOIN negocios n ON n.id = r.id_negocio
            WHERE r.link_publico = %s
            LIMIT 1
        """, (link,))
        row = cur.fetchone()
        if row:
            con.close()
            return row

    # 3) fuzzy por nombre del negocio (muy laxo)
    cur.execute("""
        SELECT * FROM negocios 
         WHERE LOWER(nombre_negocio) LIKE LOWER(%s)
         ORDER BY id DESC LIMIT 1
    """, (f"%{txt[:40]}%",))
    row = cur.fetchone()
    con.close()
    return row

def bot_menu_text(negocio):
    nom = negocio.get("nombre_negocio", "tu negocio")
    return (
        f"🤖 *GEICACONTROLRIFAS* — {nom}\n"
        f"Elige una opción:\n"
        f"1) Comprar números\n"
        f"2) Ver estado de un número\n"
        f"3) Ver rifas activas\n"
        f"4) Ayuda\n\n"
        f"Puedes escribir: *comprar*, *estado 1234*, *rifas*, *ayuda*"
    )

def bot_rifas_activas_text(negocio_id, base_url):
    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, nombre, link_publico, valor_numero, estado
          FROM rifas 
         WHERE id_negocio=%s AND estado='activa'
         ORDER BY id DESC LIMIT 6
    """, (negocio_id,))
    rows = cur.fetchall()
    con.close()
    if not rows:
        return "Por ahora no hay rifas activas."
    lines = []
    for r in rows:
        link = f"{base_url.rstrip('/')}/r/{r['link_publico']}"
        lines.append(f"• {r['nombre']} — ${r['valor_numero']:,} COP\n  {link}".replace(",", "."))
    return "🎟️ *Rifas activas:*\n" + "\n".join(lines)

def bot_estado_numero_text(negocio_id, numero):
    """
    Busca el estado de ese número en la rifa ACTIVA más reciente del negocio.
    """
    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT r.id, r.nombre
          FROM rifas r
         WHERE r.id_negocio=%s AND r.estado='activa'
         ORDER BY r.id DESC LIMIT 1
    """, (negocio_id,))
    rifa = cur.fetchone()
    if not rifa:
        con.close()
        return "No hay rifas activas en este momento."

    cur.execute("""
        SELECT estado, id_comprador 
          FROM numeros 
         WHERE id_rifa=%s AND numero=%s
         LIMIT 1
    """, (rifa["id"], numero))
    row = cur.fetchone()
    con.close()

    if not row:
        return f"El número *{numero}* no existe en la rifa activa."
    est = row["estado"]
    if est == "disponible":
        return f"El número *{numero}* está *DISPONIBLE* ✅"
    if est == "reservado":
        return f"El número *{numero}* está *RESERVADO* ⏳"
    if est == "pagado":
        return f"El número *{numero}* ya fue *VENDIDO* ❌"
    return f"El número *{numero}* está en estado: {est}"

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


def absolute_static(path: str) -> str:
    """
    Devuelve URL absoluta para recursos estáticos.
    Si no hay contexto de request (raro en webhook, pero por si acaso),
    usa BASE_URL como fallback.
    """
    import os
    try:
        return url_for("static", filename=path, _external=True)
    except RuntimeError:
        base = os.getenv("BASE_URL", "https://geicacontrolrifas.onrender.com").rstrip("/")
        return f"{base}/static/{path}"

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

    # Wompi
    pub          = (request.form.get("public_key_wompi") or "").strip()
    prv          = (request.form.get("private_key_wompi") or "").strip()
    itg          = (request.form.get("integrity_secret_wompi") or "").strip()
    chk          = (request.form.get("checkout_url_wompi") or "").strip()

    # Bot/WhatsApp (NUEVO)
    wa_numero_receptor = (request.form.get("wa_numero_receptor") or "").strip()
    bot_config_raw     = (request.form.get("bot_config") or "").strip()

    # Validaciones mínimas
    if not nombre or not correo or not contrasena:
        flash("Nombre, correo y contraseña son obligatorios.", "warning")
        return redirect(url_for("superadmin_panel"))

    # Aceptar llaves de producción O llaves de prueba (sin cambiar tu lógica)
    if not (
        (pub.startswith("pub_prod_") and prv.startswith("prv_prod_") and itg.startswith("prod_integrity_")) or
        (pub.startswith("pub_test_") and prv.startswith("prv_test_") and itg.startswith("test_integrity_"))
    ):
        flash("Debes registrar llaves Wompi válidas (producción o prueba).", "danger")
        return redirect(url_for("superadmin_panel"))

    # Parseo seguro de bot_config (JSON opcional)
    bot_config = None
    if bot_config_raw:
        try:
            import json
            bot_config = json.loads(bot_config_raw)
            if not isinstance(bot_config, dict):
                bot_config = {"fallback": str(bot_config)}
        except Exception:
            # Si el JSON viene mal, lo guardamos como texto en clave fallback
            bot_config = {"fallback": bot_config_raw}

    try:
        con = db()
        cur = con.cursor()

        # Duplicado por correo
        cur.execute("SELECT 1 FROM negocios WHERE correo = %s", (correo,))
        if cur.fetchone():
            con.close()
            flash("Ese correo ya está registrado.", "danger")
            return redirect(url_for("superadmin_panel"))

        # INSERT con columnas nuevas: wa_numero_receptor, bot_config
        cur.execute("""
            INSERT INTO negocios
                (nombre_negocio, nombre_propietario, celular, correo, contrasena,
                 public_key_wompi, private_key_wompi, integrity_secret_wompi,
                 checkout_url_wompi, estado, wa_numero_receptor, bot_config)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            nombre, propietario or nombre, celular, correo, contrasena,
            pub, prv, itg, chk, estado,
            wa_numero_receptor or None,
            bot_config  # psycopg2 serializa dict→jsonb si la columna es JSON/JSONB
        ))
        con.commit()
        con.close()
        flash("Negocio creado ✅", "success")
    except Exception as e:
        try:
            con.rollback(); con.close()
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

        # 📅 nueva: fecha/hora del sorteo (opcional)
        fecha_fin_str = (request.form.get("fecha_fin") or "").strip()
        fecha_fin = None
        if fecha_fin_str:
            try:
                # datetime-local => 'YYYY-MM-DDTHH:MM'
                fecha_fin = datetime.fromisoformat(fecha_fin_str)
            except Exception:
                fecha_fin = None  # si viene mal, guardamos NULL

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
            INSERT INTO rifas (
                id_negocio, nombre, descripcion, avaluo, cifras, cantidad_numeros,
                valor_numero, nombre_loteria, imagen_premio, link_publico, estado, fecha_fin
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'activa', %s)
            RETURNING id
        """, (
            negocio["id"], nombre, descripcion, avaluo, cifras, cantidad,
            valor, loteria, imagen_premio, link_publico, fecha_fin
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
from urllib.parse import quote  # al inicio del archivo o cerca de otros imports

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

    # ===== BOT WHATSAPP: construir link al número del bot =====
    # 1) Número BOT: por negocio (wa_numero_receptor) o global .env (TWILIO_PHONE)
    wa_bot = (negocio.get("wa_numero_receptor") or os.getenv("TWILIO_PHONE") or "").strip()
    # normaliza: quita 'whatsapp:' y '+'
    wa_clean = wa_bot.replace("whatsapp:", "").replace("+", "").replace(" ", "")

    # 2) Mensaje inicial con tags para que el bot identifique negocio/rifa
    #    (el bot verá esto en /bot/webhook o /wa/webhook)
    wa_msg = f"GEICA_NEGOCIO:{negocio['id']}\nGEICA_RIFA:{rifa['id']}"
    wa_link = ""
    if wa_clean:
        wa_link = f"https://wa.me/{wa_clean}?text={quote(wa_msg)}"

    # 3) Pasar también la base absoluta (meta OG)
    app_base_url = (os.getenv("APP_BASE_URL") or request.host_url or "").rstrip("/")

    return render_template(
        "rifa_publica.html",
        rifa=rifa,
        negocio=negocio,
        numeros=numeros_fmt,
        wa_link=wa_link,
        app_base_url=app_base_url
    )

# ------- GENERAR PAGO (SELECCIÓN + DATOS CLIENTE) ---
@app.route("/generar-pago", methods=["POST"])
def generar_pago():
    """
    1) Valida datos y rifa activa
    2) Limpia reservas vencidas
    3) Verifica disponibilidad y RESERVA con expiración
    4) Crea/actualiza comprador y compra 'pendiente'
    5) Genera link Wompi (producción o sandbox)
    * Ajuste: Comisión Wompi 50/50 -> al monto cobrado al comprador se suma la mitad de la comisión estimada.
      - La comisión se estima como: total * WOMPI_FEE_PCT + WOMPI_FEE_FIX
      - WOMPI_FEE_PCT (float, ej 0.0299) y WOMPI_FEE_FIX (int, ej 900) vienen de variables de entorno.
      - compras.total se mantiene como el valor base (sin recargo), para que tu contabilidad interna no cambie.
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

    # 4b) Crear compra pendiente (total BASE sin recargo; NO tocamos tu contabilidad)
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

    # ====== AJUSTE 50/50 COMISIÓN WOMPI (solo para lo que paga el comprador) ======
    # Estimación configurable por entorno (por defecto 2.99% + 900 COP)
    try:
        fee_pct = float(os.getenv("WOMPI_FEE_PCT", "0.0299"))
    except Exception:
        fee_pct = 0.0299
    try:
        fee_fix = int(os.getenv("WOMPI_FEE_FIX", "900"))
    except Exception:
        fee_fix = 900

    # Comisión estimada total (para toda la compra)
    fee_estimada = int(round(total * fee_pct + fee_fix))
    # Mitad para el comprador (se suma al cobro)
    recargo_cliente = max(0, fee_estimada // 2)

    monto_cobrar_cents = (total + recargo_cliente) * 100
    # ==============================================================================

    referencia  = f"compra_{compra_id}"
    descripcion = f"Rifa {rifa['nombre']} - Números {numeros_str} (incluye comisión compartida)"

    try:
        checkout_url = generar_link_de_pago(
            amount_in_cents=monto_cobrar_cents,
            currency="COP",
            reference=referencia,
            description=descripcion,
            customer_email=correo,
            wompi_public_key=pub,
            wompi_private_key=prv,
            wompi_env=wompi_env,                          # producción o sandbox
            wompi_integrity_secret=itg,
            wompi_checkout_base=chk or "https://checkout.wompi.co/p/"
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

@app.before_request
def _auto_archivar_hook():
    # Corre en cada request (consulta rápida). Si quieres,
    # podrías cachear por minuto con session o app.config.
    archivar_rifas_vencidas()

# ---------------- WEBHOOK PAGO ----------------------
@app.route("/webhook-pago", methods=["POST"])
def webhook_pago():
    """
    Wompi envía un evento -> verificamos y cerramos compra:
    - Si 'APPROVED' -> compra=pagado, números=pagado, reservado_hasta=NULL
    - Si 'DECLINED' / 'VOIDED' / 'ERROR' -> números=disponible, id_comprador=NULL, reservado_hasta=NULL
    Además: envía WhatsApp y correos HTML (cliente y admin) con logo y datos dinámicos.
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

    try:
        compra_id = int(reference.split("_")[1])
    except Exception:
        con.close()
        return jsonify({"ok": True}), 200

    # Traemos también link_publico para el correo del cliente
    cur.execute("""
        SELECT c.*, r.id_negocio, r.nombre, r.valor_numero, r.id AS rifa_id, r.link_publico
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

        # 3) notificar (WhatsApp + correos HTML con logo/plantillas)
        try:
            # --- WhatsApp (simple, como ya lo tenías) ---
            msg_cli = (f"🎉 ¡Pago aprobado!\nRifa: {compra['nombre']}\n"
                       f"Números: {compra['numeros']}\nTotal: ${compra['total']}\n¡Suerte!")
            if comprador and comprador.get("telefono"):
                enviar_whatsapp(comprador["telefono"], msg_cli)

            msg_admin = (f"✅ Pago recibido\nCliente: {comprador.get('nombre', '—')}\n"
                         f"Números: {compra['numeros']}\nTotal: ${compra['total']}")
            if negocio and negocio.get("celular"):
                enviar_whatsapp(negocio["celular"], msg_admin)

            # --- Correos bonitos (HTML) con plantillas ---
            # Base absoluta para recursos en correo
            base_url = (os.getenv("APP_BASE_URL") or request.host_url or "").rstrip("/")
            logo_url = f"{base_url}/static/img/geica-logo.png"
            link_publico = f"{base_url}/r/{compra['link_publico']}"
            link_admin = f"{base_url}/panel"

            # Contexto para las plantillas
            ctx = {
                "logo_url": logo_url,
                "rifa": {"nombre": compra["nombre"]},
                "compra": compra,
                "negocio": negocio,
                "comprador": comprador,
                "numeros_lista": numeros_lista,
                "link_publico": link_publico,
                "link_admin": link_admin,
            }

            # Cliente (si tiene correo)
            if comprador and comprador.get("correo"):
                html_cliente = render_template("email_compra.html", **ctx)
                enviar_correo(comprador["correo"], "🎉 Compra confirmada", html_cliente)

            # Admin del negocio (si tiene correo)
            if negocio and negocio.get("correo"):
                html_admin = render_template("email_admin.html", **ctx)
                enviar_correo(negocio["correo"], "✅ Nueva compra confirmada", html_admin)

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
    return {
        "soporte_url": SOPORTE_URL,
        "app_base_url": (os.getenv("APP_BASE_URL") or request.host_url or "").rstrip("/")
    }

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
# ---------------- WHATSAPP BOT (Twilio Webhook) ----------------------
def _clean_wa(s: str) -> str:
    if not s: 
        return ""
    s = s.strip()
    return s.replace("whatsapp:", "").replace(" ", "")

def _base_url() -> str:
    return (os.getenv("APP_BASE_URL") or request.host_url or "").rstrip("/")

def _render_template_text(tpl: str, negocio: dict, rifa: dict | None = None) -> str:
    # Mini render de placeholders sencillos
    out = tpl or ""
    pairs = {
        "{{negocio.nombre_negocio}}": (negocio or {}).get("nombre_negocio", ""),
        "{{negocio.celular}}":       (negocio or {}).get("celular", ""),
        "{{negocio.correo}}":        (negocio or {}).get("correo", ""),
        "{{app.base_url}}":          _base_url(),
    }
    if rifa:
        pairs.update({
            "{{rifa.nombre}}":        rifa.get("nombre", ""),
            "{{rifa.valor_numero}}":  str(rifa.get("valor_numero", "")),
        })
    for k, v in pairs.items():
        out = out.replace(k, str(v))
    return out

@app.post("/bot/whatsapp")
def bot_whatsapp():
    """
    Webhook Twilio (WhatsApp). Se adapta por negocio:
    - Primero intenta identificar por 'To' (un número de Twilio por negocio) -> Modo A.
    - Si no se puede, intenta deducir por el cuerpo del mensaje (@alias, /r/<link>, nombre) -> Modo B.
    Responde con intents básicos: menu, comprar, estado <num>, rifas, ayuda.
    """
    # Twilio manda datos en form-encoded
    body = (request.form.get("Body") or "").strip()
    wa_to = (request.form.get("To") or "").strip()       # whatsapp:+1415...
    wa_from = (request.form.get("From") or "").strip()   # whatsapp:+57...

    # 1) Identificar negocio
    negocio = find_negocio_by_twilio_to(wa_to) or find_negocio_by_hint(body)
    if not negocio:
        # Menú genérico si no logramos mapear
        resp = MessagingResponse()
        resp.message("Hola 👋\nNo pude identificar el negocio. Por favor escribe *@NOMBRE* del negocio o pega el *link público* de la rifa.")
        return str(resp)

    base_url = (os.getenv("APP_BASE_URL") or request.host_url or "").rstrip("/")

    txt = body.lower()

    # Intents directos
    if txt in ("menu", "hola", "hi", "buenas", "start", "inicio", "ayuda"):
        reply = bot_menu_text(negocio)

    elif txt.startswith("comprar") or txt in ("1", "compra"):
        # devolver rifas + CTA
        reply = bot_rifas_activas_text(negocio["id"], base_url)

    elif txt.startswith("rifas") or txt == "3":
        reply = bot_rifas_activas_text(negocio["id"], base_url)

    elif txt.startswith("estado") or txt == "2":
        # estado 05  / estado 1234
        m = re.search(r'(?:estado\s+)?([0-9]{1,6})', txt)
        if m:
            numero = m.group(1)
            reply = bot_estado_numero_text(negocio["id"], numero)
        else:
            reply = "Escribe: *estado 05* (o el número que quieras consultar)."

    elif re.fullmatch(r'[0-9]{1,6}', txt):
        # Si solo envían un número, asumimos consulta de estado:
        reply = bot_estado_numero_text(negocio["id"], txt)

    else:
        # Fallback configurable por negocio (bot_config) o menú
        fallback = None
        try:
            # Si guardaste JSON en negocio.bot_config, puedes leer una respuesta por defecto:
            # { "fallback": "Mensaje de ayuda..." }
            bc = negocio.get("bot_config")
            if isinstance(bc, dict):
                fallback = bc.get("fallback")
        except Exception:
            pass
        reply = fallback or bot_menu_text(negocio)

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.post("/wa/webhook")
def wa_webhook():
    """
    Webhook de Twilio (WhatsApp). Multi-negocio:
    - Detecta negocio por el 'To' (tu número en Twilio).
    - Responde intents básicos: 1) Ver rifas, 2) Disponibles, 3) Precio, 4) Ayuda
    - Fallback configurable por negocio (JSON en negocios.bot_config)
    """
    # Twilio manda x-www-form-urlencoded
    body = (request.form.get("Body") or "").strip().lower()
    wa_from = _clean_wa(request.form.get("From") or "")   # 'whatsapp:+57...' → '+57...'
    wa_to   = _clean_wa(request.form.get("To") or "")     # TU número Twilio (por negocio)

    # 1) Resolver negocio por 'To'
    con = db(); cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT * FROM negocios 
         WHERE replace(coalesce(wa_numero_receptor, ''), ' ', '') = %s
            OR replace(coalesce(wa_numero_receptor, ''), ' ', '') = %s
            OR replace(coalesce(celular, ''), ' ', '') = %s
         ORDER BY wa_numero_receptor DESC NULLS LAST
         LIMIT 1
    """, (wa_to, f"+{wa_to.lstrip('+')}", wa_to))
    negocio = cur.fetchone()

    if not negocio:
        con.close()
        # Sin negocio: respondemos genérico para no romper flujo
        enviar_whatsapp(wa_from, "👋 Hola, no encuentro un negocio asociado a este número de WhatsApp.")
        return ("", 204)

    # 2) Cargar config de bot (JSONB) o defaults
    bot_cfg = negocio.get("bot_config") or {}
    if isinstance(bot_cfg, str):
        import json
        try: bot_cfg = json.loads(bot_cfg)
        except Exception: bot_cfg = {}

    greeting = bot_cfg.get("greeting") or "👋 ¡Hola! Soy el asistente de {{negocio.nombre_negocio}}.\n1️⃣ Ver rifas · 2️⃣ Disponibles · 3️⃣ Precio · 4️⃣ Ayuda"
    menu     = bot_cfg.get("menu") or "1️⃣ Ver rifas · 2️⃣ Disponibles · 3️⃣ Precio · 4️⃣ Ayuda"
    intents  = bot_cfg.get("intents") or {}
    fallback = bot_cfg.get("fallback") or "No te entendí 🤖. Responde con 1, 2, 3 o 4."

    # 3) Normalizar intención (números o palabras)
    intent_key = None
    if body in ("hola", "buenas", "hi", "hello", "menu", "menú", "inicio", "start"):
        intent_key = "greet"
    elif body in ("1", "rifas", "ver rifas"):
        intent_key = "rifas"
    elif body in ("2", "disponibles", "disponible"):
        intent_key = "disponibles"
    elif body in ("3", "precio", "precios", "valor"):
        intent_key = "precio"
    elif body in ("4", "ayuda", "soporte"):
        intent_key = "ayuda"
    elif "comprar" in body or "pagar" in body:
        intent_key = "comprar"

    # 4) Ejecutar intención
    base_url = _base_url()

    # Helper: última rifa activa del negocio
    def _ultima_rifa():
        cur.execute("""
            SELECT * FROM rifas 
             WHERE id_negocio=%s AND estado='activa'
             ORDER BY id DESC LIMIT 1
        """, (negocio["id"],))
        return cur.fetchone()

    # Helper: link público por rifa
    def _link_publico(r):
        if not r: 
            return ""
        return f"{base_url}/r/{r['link_publico']}"

    # Intent greet / menú
    if intent_key in (None, "greet"):
        msg = _render_template_text(greeting, negocio)
        enviar_whatsapp(wa_from, msg)
        enviar_whatsapp(wa_from, menu)
        con.close()
        return ("", 204)

    # Intent listar rifas
    if intent_key == "rifas":
        cur.execute("""
            SELECT nombre, link_publico, valor_numero
              FROM rifas
             WHERE id_negocio=%s AND estado='activa'
             ORDER BY id DESC
             LIMIT 6
        """, (negocio["id"],))
        rifas = cur.fetchall()
        if not rifas:
            enviar_whatsapp(wa_from, "No hay rifas activas en este momento.")
        else:
            lines = ["🎟️ Rifas activas:"]
            for r in rifas:
                lines.append(f"• {r['nombre']} — $ {r['valor_numero']} COP\n{base_url}/r/{r['link_publico']}")
            lines.append("\n" + menu)
            enviar_whatsapp(wa_from, "\n".join(lines))
        con.close()
        return ("", 204)

    # Intent disponibles (usa última rifa si no está configurado algo distinto)
    if intent_key == "disponibles":
        r = _ultima_rifa()
        if not r:
            enviar_whatsapp(wa_from, "No encuentro rifas activas.")
            con.close(); return ("", 204)
        # contar disponibles
        cur.execute("""
            SELECT COUNT(*) AS libres
              FROM numeros
             WHERE id_rifa=%s AND estado='disponible'
        """, (r["id"],))
        libres = cur.fetchone()["libres"]
        enviar_whatsapp(wa_from, f"🔢 Disponibles en *{r['nombre']}*: {libres}\n{_link_publico(r)}")
        enviar_whatsapp(wa_from, menu)
        con.close(); return ("", 204)

    # Intent precio (última rifa)
    if intent_key == "precio":
        r = _ultima_rifa()
        if not r:
            enviar_whatsapp(wa_from, "No encuentro rifas activas.")
            con.close(); return ("", 204)
        tpl = intents.get("precio", {}).get("template") or "Cada número vale ${{rifa.valor_numero}} COP"
        enviar_whatsapp(wa_from, _render_template_text(tpl, negocio, r))
        enviar_whatsapp(wa_from, _link_publico(r))
        con.close(); return ("", 204)

    # Intent comprar: redirige al link público de la última rifa
    if intent_key == "comprar":
        r = _ultima_rifa()
        if not r:
            enviar_whatsapp(wa_from, "No encuentro rifas activas.")
            con.close(); return ("", 204)
        enviar_whatsapp(wa_from, f"💳 Para comprar ingresa aquí:\n{_link_publico(r)}")
        con.close(); return ("", 204)

    # Intent ayuda
    if intent_key == "ayuda":
        tpl = intents.get("ayuda", {}).get("template") or "Escríbenos a {{negocio.celular}} o {{negocio.correo}}"
        enviar_whatsapp(wa_from, _render_template_text(tpl, negocio))
        con.close(); return ("", 204)

    # Fallback
    enviar_whatsapp(wa_from, fallback)
    enviar_whatsapp(wa_from, menu)
    con.close()
    return ("", 204)

import re
from urllib.parse import urlparse

@app.post("/bot/webhook")
def bot_webhook():
    """
    Webhook de WhatsApp (Twilio).
    - Resuelve negocio por 'To' (número receptor).
    - Detecta rifa por link público en el texto (si viene).
    - Responde con:
        * Mensaje de bienvenida configurable (bot_config).
        * Resumen de la rifa (precio, números vendidos / disponibles, link público).
    """
    # Twilio form-encoded payload
    to_raw   = (request.form.get("To") or "").strip()        # whatsapp:+57...
    from_raw = (request.form.get("From") or "").strip()      # whatsapp:+57...
    body     = (request.form.get("Body") or "").strip()

    # 1) Normalizar receptor (To) para buscar negocio
    to_norm = to_raw.replace("whatsapp:", "")
    to_norm = to_norm.replace(" ", "")

    con = db()
    cur = con.cursor(cursor_factory=RealDictCursor)

    # 2) Buscar negocio por su número receptor (wa_numero_receptor)
    #    Si aún no llenaste esa columna, también puedes usar 'celular'.
    cur.execute("""
        SELECT *
          FROM negocios
         WHERE REPLACE(COALESCE(wa_numero_receptor, celular), ' ', '') = %s
           AND estado='activo'
         LIMIT 1
    """, (to_norm,))
    negocio = cur.fetchone()

    # Si no encontramos por 'To', igual intentamos sin bloquear (multi-tenant con 1 número compartido),
    # en tal caso negocio quedará None y buscaremos por rifa.
    # 3) Intentar extraer link_publico de un URL en el Body
    link_publico = None
    try:
        # Buscar cualquier URL y si path coincide con /r/<slug>, extraemos el slug
        urls = re.findall(r'(https?://[^\s]+)', body)
        for u in urls:
            p = urlparse(u)
            # esperamos /r/<link_publico>
            parts = p.path.rstrip("/").split("/")
            if len(parts) >= 3 and parts[-2] == "r":  # .../r/<slug>
                link_publico = parts[-1]
                break
    except Exception:
        pass

    rifa = None
    if link_publico:
        cur.execute("SELECT * FROM rifas WHERE link_publico=%s AND estado='activa' LIMIT 1", (link_publico,))
        rifa = cur.fetchone()
        # si el negocio no estaba resuelto por To, úsalo desde la rifa:
        if rifa and not negocio:
            cur.execute("SELECT * FROM negocios WHERE id=%s AND estado='activo'", (rifa["id_negocio"],))
            negocio = cur.fetchone()

    # 4) Cargar bot_config (JSON) si existe
    fallback = "¡Hola! 👋 Gracias por escribir a GEICACONTROLRIFAS."
    if negocio and negocio.get("bot_config"):
        # bot_config es TEXT, guardamos JSON; parsearlo:
        try:
            import json
            cfg = json.loads(negocio["bot_config"])
            fallback = (cfg.get("fallback") or fallback).strip()
        except Exception:
            pass

    # 5) Generar respuesta
    reply_lines = []

    # Bienvenida siempre
    reply_lines.append(fallback or "¡Hola! 👋")

    # Si detectamos rifa, agregar resumen real-time
    if rifa:
        # vender / disponibles
        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE estado='pagado')      AS vendidos,
              COUNT(*) FILTER (WHERE estado='disponible')  AS disponibles,
              COUNT(*)                                      AS total
            FROM numeros
            WHERE id_rifa=%s
        """, (rifa["id"],))
        stats = cur.fetchone() or {"vendidos": 0, "disponibles": 0, "total": 0}

        # links
        base_url = (os.getenv("APP_BASE_URL") or request.host_url or "").rstrip("/")
        link_rifa = f"{base_url}/r/{rifa['link_publico']}"

        reply_lines.append(
            f"📌 *Rifa:* {rifa['nombre']}\n"
            f"💰 *Precio por número:* ${rifa['valor_numero']:,} COP\n"
            f"🧮 *Vendidos:* {stats['vendidos']}  |  *Disponibles:* {stats['disponibles']}\n"
            f"🔗 Link: {link_rifa}"
        )
        reply_lines.append("¿Te ayudo a separar tus números? Escribe *comprar* para continuar. 🧾")
    else:
        # si no hay rifa detectada, ofrecer ayuda genérica
        reply_lines.append("Envíame el *link* de la rifa o escribe una palabra clave: *precio*, *comprar*, *métodos de pago*.")

    con.close()

    # 6) Responder al cliente
    respuesta = "\n\n".join(reply_lines)
    enviar_whatsapp(from_raw, respuesta)
    return ("", 204)

# ================== MAIN ============================
if __name__ == "__main__":
    print(">>> Arrancando servidor Flask en puerto 10000...")
    app.run(host="0.0.0.0", debug=True, port=10000)