import os
from dotenv import load_dotenv
from twilio.rest import Client
import smtplib
from email.mime.text import MIMEText

load_dotenv()

# 🔐 Twilio
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")  # ej: 'whatsapp:+14155238886'

# 📧 Email
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

def _format_wa_number(n: str) -> str:
    """Devuelve 'whatsapp:+57XXXXXXXXXX' si falta el prefijo."""
    if not n:
        return n
    n = n.strip()
    if not n.startswith("whatsapp:"):
        # si viene sin +57, lo agregamos (ajusta a tu país si quieres)
        if not n.startswith("+"):
            n = "+57" + n
        n = "whatsapp:" + n
    return n

def enviar_whatsapp(*args, **kwargs):
    """
    Soporta:
      - enviar_whatsapp(numero, mensaje)  ← recomendado
      - enviar_whatsapp(mensaje, numero)  ← legacy
    """
    if len(args) == 2:
        # adivinar orden: si el primero parece número, lo tratamos como número
        a, b = args
        if isinstance(a, str) and (a.startswith("+") or a.replace(" ", "").isdigit() or a.startswith("whatsapp:")):
            numero_destino, mensaje = a, b
        else:
            mensaje, numero_destino = a, b
    else:
        numero_destino = kwargs.get("numero") or kwargs.get("to")
        mensaje = kwargs.get("mensaje") or kwargs.get("body") or ""

    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        to_wa = _format_wa_number(numero_destino)
        message = client.messages.create(
            from_=TWILIO_PHONE,
            body=mensaje,
            to=to_wa
        )
        return f"WhatsApp enviado: SID {message.sid}"
    except Exception as e:
        return f"Error WhatsApp: {e}"

def enviar_correo(destinatario, asunto, cuerpo_html):
    try:
        msg = MIMEText(cuerpo_html, 'html')
        msg['Subject'] = asunto
        msg['From'] = EMAIL_USER
        msg['To'] = destinatario

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASSWORD)
            smtp.send_message(msg)
        return f"Correo enviado a {destinatario}"
    except Exception as e:
        return f"Error Correo: {e}"
