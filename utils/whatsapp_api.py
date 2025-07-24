import os
from twilio.rest import Client
from dotenv import load_dotenv
from twilio.rest import Client
import os
from dotenv import load_dotenv

load_dotenv()

twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
twilio_whatsapp = os.getenv("TWILIO_WHATSAPP_NUMBER")


load_dotenv()

def enviar_mensaje_whatsapp(numero_destino, mensaje):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_whatsapp = os.getenv("TWILIO_WHATSAPP_NUMBER")

    client = Client(account_sid, auth_token)
    client.messages.create(
        body=mensaje,
        from_=from_whatsapp,
        to=f"whatsapp:+{numero_destino}"
    )

def enviar_whatsapp_admin(numero_admin, mensaje):
    try:
        numero_formateado = f'whatsapp:+57{numero_admin[-10:]}'  # Se asume número colombiano
        message = twilio_client.messages.create(
            body=mensaje,
            from_=twilio_whatsapp,
            to=numero_formateado
        )
        print(f"Mensaje enviado con SID: {message.sid}")
        return True
    except Exception as e:
        print(f"Error al enviar WhatsApp: {e}")
        return False
