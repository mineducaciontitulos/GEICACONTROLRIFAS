import os
from dotenv import load_dotenv
from twilio.rest import Client
import smtplib
from email.mime.text import MIMEText

load_dotenv()

# 🔐 Twilio
TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
# Debe venir en formato 'whatsapp:+14155238886' o el que tengas
TWILIO_PHONE = os.getenv("TWILIO_PHONE", "").strip()   # remitente WA

# 📧 Email (Gmail SSL 465 como tienes ahora)
EMAIL_USER     = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "").strip()

# ✅ Fallbacks opcionales: SOLO se usan si no llega destinatario a la función
NOTIF_EMAIL_TO = os.getenv("NOTIF_EMAIL_TO", "").strip()  # ej. correo del dueño de la plataforma
NOTIF_WA_TO    = os.getenv("NOTIF_WA_TO", "").strip()     # ej. 57XXXXXXXXXX (sin 'whatsapp:')

def _format_wa_number(n: str) -> str:
    """
    Devuelve 'whatsapp:+<num>' respetando el país que ya traiga el número.
    - Acepta: '300123...', '+57300123...', 'whatsapp:+57...', '  57 300 123  ...'
    - Si no trae '+' ni 'whatsapp:', NO agrega país por defecto (para no romper países no-CO).
      Puedes forzar un país por defecto con WA_DEFAULT_CC si lo necesitas.
    """
    if not n:
        return n
    n = n.strip().replace(" ", "")
    if n.startswith("whatsapp:"):
        return n
    if n.startswith("+"):
        return f"whatsapp:{n}"
    # Si quieres forzar por defecto a +57, descomenta las 2 líneas siguientes:
    # n = "+57" + n
    # return f"whatsapp:{n}"
    # Por defecto, si no trae '+', asumimos que ya está completo en tu cuenta de Twilio (no lo forzamos).
    return f"whatsapp:{n}"

def _twilio_config_ok() -> bool:
    ok = bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_PHONE)
    if not ok:
        print("[WA][CONFIG] Faltan TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN o TWILIO_PHONE")
    return ok

def enviar_whatsapp(*args, **kwargs):
    """
    Soporta:
      - enviar_whatsapp(numero, mensaje)  ← recomendado
      - enviar_whatsapp(mensaje, numero)  ← legacy
      - enviar_whatsapp(numero=..., mensaje=...) / (to=..., body=...)
    Retorna string como en tu versión original.
    """
    # --- Parseo compatible con tu legado ---
    numero_destino = None
    mensaje = ""

    if len(args) == 2:
        a, b = args
        # Heurística: si 'a' parece número, es número
        if isinstance(a, str) and (a.startswith("+") or a.startswith("whatsapp:") or a.replace(" ", "").isdigit()):
            numero_destino, mensaje = a, b
        else:
            mensaje, numero_destino = a, b
    else:
        numero_destino = kwargs.get("numero") or kwargs.get("to")
        mensaje = kwargs.get("mensaje") or kwargs.get("body") or ""

    # --- Fallback de destinatario si no llega ---
    if not numero_destino:
        numero_destino = NOTIF_WA_TO
        if not numero_destino:
            return "[WA] SIN DESTINATARIO (ni parámetro ni NOTIF_WA_TO). No se envía."

    # --- Validar config Twilio ---
    if not _twilio_config_ok():
        return "[WA] Config Twilio incompleta. No se envía."

    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        to_wa = _format_wa_number(numero_destino)
        if not TWILIO_PHONE.startswith("whatsapp:"):
            # Si el from no está en formato whatsapp:, lo normalizamos (no rompemos si ya venía bien)
            from_wa = TWILIO_PHONE if TWILIO_PHONE.startswith("whatsapp:") else f"whatsapp:{TWILIO_PHONE}"
        else:
            from_wa = TWILIO_PHONE

        msg = client.messages.create(
            from_=from_wa,
            body=mensaje,
            to=to_wa
        )
        info = f"WhatsApp enviado a {to_wa}: SID {msg.sid}"
        print(f"[WA] {info}")
        return info
    except Exception as e:
        err = f"Error WhatsApp: {e}"
        print(f"[WA][ERROR] {err}")
        return err

def enviar_correo(destinatario, asunto, cuerpo_html):
    """
    Envía al destinatario recibido; si viene vacío, usa NOTIF_EMAIL_TO.
    Mantiene tu transporte: Gmail SSL 465.
    Retorna string de éxito/error (como ahora).
    """
    to = (destinatario or "").strip() or NOTIF_EMAIL_TO
    if not to:
        return "[EMAIL] SIN DESTINATARIO (ni parámetro ni NOTIF_EMAIL_TO). No se envía."

    if not (EMAIL_USER and EMAIL_PASSWORD):
        return "[EMAIL] Falta EMAIL_USER/EMAIL_PASSWORD. No se envía."

    try:
        # Aseguramos utf-8; si no es HTML, igual se verá bien
        is_html = "<" in cuerpo_html and ">" in cuerpo_html
        msg = MIMEText(cuerpo_html, "html" if is_html else "plain", "utf-8")
        msg["Subject"] = asunto
        msg["From"] = EMAIL_USER
        msg["To"] = to

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASSWORD)
            smtp.send_message(msg)

        info = f"Correo enviado a {to} (asunto='{asunto}')"
        print(f"[EMAIL] {info}")
        return info
    except Exception as e:
        err = f"Error Correo: {e}"
        print(f"[EMAIL][ERROR] {err}")
        return err