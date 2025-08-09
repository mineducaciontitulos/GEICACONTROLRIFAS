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
    wompi_private_key: str,   # no se usa en la URL, pero lo exigimos para validar setup
    wompi_env: str = "sandbox",
    redirect_url: str | None = None,
    # valores por negocio (opcionales)
    wompi_integrity_secret: str | None = None,
    wompi_checkout_base: str | None = None,
) -> str:
    """
    Genera URL de Checkout (/p/) con firma de integridad.
    Cambio mínimo: la firma ahora es SHA-256 de la concatenación
    reference + amount_in_cents + currency + integrity_secret (no HMAC).
    """

    # --- Validaciones básicas
    if not wompi_public_key or not wompi_public_key.startswith("pub_"):
        raise ValueError("Wompi public key inválida (pub_test_* o pub_prod_* requerida).")

    try:
        amount_in_cents = int(amount_in_cents)
    except Exception:
        raise ValueError("amount_in_cents debe ser entero > 0")
    if amount_in_cents <= 0:
        raise ValueError("amount_in_cents debe ser > 0")

    # --- Base del checkout (forzar /p/)
    base = (wompi_checkout_base or os.getenv("WOMPI_CHECKOUT_URL") or "https://checkout.wompi.co/p/").strip()
    if "/l/" in base:
        base = "https://checkout.wompi.co/p/"
    if not base.endswith("/"):
        base += "/"
    if not base.endswith("/p/"):
        base = "https://checkout.wompi.co/p/"

    # --- Normalización de lo que se firma y se envía
    reference = (reference or "").strip()
    currency  = (currency or "COP").strip().upper()
    amount_in_cents = int(amount_in_cents)
    redirect_url = (redirect_url or os.getenv("WOMPI_REDIRECT_URL") or "").strip()
    email = (customer_email or "").strip()

    # Log de inputs firmados (útil en sandbox; puedes quitarlo luego)
    print("[WOMPI] signing-inputs:",
          "reference=", repr(reference),
          "amount_in_cents=", amount_in_cents,
          "currency=", repr(currency), flush=True)

    # --- Parámetros del checkout (con los valores YA normalizados)
    params = {
        "public-key": wompi_public_key,
        "currency": currency,
        "amount-in-cents": amount_in_cents,
        "reference": reference,
        "redirect-url": redirect_url or None,      # se omite si está vacío
        "customer-data.email": email or None,      # se omite si está vacío
    }

    # --- Firma de integridad (mismo comercio/ambiente que la pub_)
    integrity_secret = (wompi_integrity_secret or os.getenv("WOMPI_INTEGRITY_SECRET") or "").strip()
    if integrity_secret:
        # Cadena EXACTA que Wompi espera (si algún día envías expiration-time, también se concatena ANTES del secret)
        signing_string = f"{reference}{amount_in_cents}{currency}"
        # SHA-256 SIMPLE de la concatenación (no HMAC)
        signature = hashlib.sha256((signing_string + integrity_secret).encode("utf-8")).hexdigest()
        params["signature:integrity"] = signature

    # --- Limpieza y URL final
    params = {k: v for k, v in params.items() if v not in (None, "", [])}
    url = f"{base}?{urlencode(params)}"

    # Logs de ayuda (sandbox)
    print("[WOMPI][URL]", url, flush=True)
    print("[WOMPI] hasSignature:", ("signature%3Aintegrity=" in url or "signature:integrity=" in url), flush=True)

    return url


# Alias legacy (no usar desde app.py)
def generar_link_pago(valor, referencia, descripcion, email_cliente):
    raise RuntimeError("Usa generar_link_de_pago() con llaves del negocio. Este alias está deprecado.")


def verificar_evento_webhook(evento: dict) -> dict:
    """
    Normaliza el payload del webhook de Wompi.
    Retorna: {"ok": True/False, "reference": str, "status": "APPROVED"/"DECLINED"/...}
    """
    try:
        if not isinstance(evento, dict):
            return {"ok": False, "reference": "", "status": "ERROR"}

        tx = (evento.get("data") or {}).get("transaction") or {}
        reference = tx.get("reference") or evento.get("reference") or ""
        status = tx.get("status") or "ERROR"
        return {"ok": bool(reference), "reference": reference, "status": status}
    except Exception:
        return {"ok": False, "reference": "", "status": "ERROR"}