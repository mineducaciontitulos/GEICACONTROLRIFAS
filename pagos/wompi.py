# pagos/wompi.py
import os
import hmac, hashlib
from urllib.parse import urlencode

def generar_link_de_pago(
    amount_in_cents: int,
    currency: str,
    reference: str,
    description: str,
    customer_email: str,
    wompi_public_key: str,
    wompi_private_key: str,   # no se usa en la URL, pero valida setup
    wompi_env: str = "production",            # por defecto producción
    redirect_url: str | None = None,
    wompi_integrity_secret: str | None = None,
    wompi_checkout_base: str | None = None,
) -> str:
    if not wompi_public_key or not wompi_public_key.startswith("pub_"):
        raise ValueError("Wompi public key inválida (pub_* requerida).")

    amount_in_cents = int(amount_in_cents)
    if amount_in_cents <= 0:
        raise ValueError("amount_in_cents debe ser > 0")

    base = (wompi_checkout_base or os.getenv("WOMPI_CHECKOUT_URL") or "https://checkout.wompi.co/p/").strip()
    if "/l/" in base:
        base = "https://checkout.wompi.co/p/"
    if not base.endswith("/"):
        base += "/"
    if not base.endswith("/p/"):
        base = "https://checkout.wompi.co/p/"

    reference = (reference or "").strip()
    currency  = (currency or "COP").strip().upper()
    email     = (customer_email or "").strip()
    redirect_url = (redirect_url or os.getenv("WOMPI_REDIRECT_URL") or "").strip()

    params = {
        "public-key": wompi_public_key,
        "currency": currency,
        "amount-in-cents": amount_in_cents,
        "reference": reference,
        "redirect-url": redirect_url or None,
        "customer-data.email": email or None,
    }

    integrity_secret = (wompi_integrity_secret or os.getenv("WOMPI_INTEGRITY_SECRET") or "").strip()
    if integrity_secret:
        # Firma correcta: HMAC-SHA256(key=integrity_secret, msg=reference+amount+currency)
        msg = f"{reference}{amount_in_cents}{currency}".encode("utf-8")
        sig = hmac.new(integrity_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        params["signature:integrity"] = sig

    params = {k: v for k, v in params.items() if v not in (None, "", [])}
    url = f"{base}?{urlencode(params)}"

    # Logs cortos (deja si estás afinando; quita en prod si quieres)
    print("[WOMPI][URL][PROD]", url, flush=True)
    return url

def generar_link_pago(valor, referencia, descripcion, email_cliente):
    raise RuntimeError("Usa generar_link_de_pago(). Alias deprecado.")

def verificar_evento_webhook(evento: dict) -> dict:
    try:
        if not isinstance(evento, dict):
            return {"ok": False, "reference": "", "status": "ERROR"}
        tx = (evento.get("data") or {}).get("transaction") or {}
        reference = tx.get("reference") or evento.get("reference") or ""
        status = tx.get("status") or "ERROR"
        return {"ok": bool(reference), "reference": reference, "status": status}
    except Exception:
        return {"ok": False, "reference": "", "status": "ERROR"}