"""
SinergyOS Billing — Stripe Checkout + Webhook.
Endpoints bajo /sinergy/billing.
"""
import os
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from db import SessionLocal

router = APIRouter(prefix="/sinergy/billing", tags=["Billing"])

STRIPE_SECRET_KEY    = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
BASE_URL             = os.getenv("SINERGY_BASE_URL", "http://localhost:3003/sinergy")

PRICE_IDS = {
    "basic": os.getenv("STRIPE_PRICE_BASIC", "price_1TUNE4DM3KbBor3xJiEFRgCR"),
    "pro":   os.getenv("STRIPE_PRICE_PRO",   "price_1TUNE5DM3KbBor3xV3YpcNxQ"),
}


def _stripe():
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


# ── Checkout ──────────────────────────────────────────────────────────────────

def create_checkout_url(tenant_id: int, slug: str, plan: str, email: str | None) -> str:
    stripe = _stripe()
    price_id = PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(400, f"Plan sin precio Stripe configurado: {plan}")

    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{BASE_URL}/register/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url":  f"{BASE_URL}/register/cancel",
        "metadata":    {"tenant_id": str(tenant_id), "tenant_slug": slug},
        "subscription_data": {"metadata": {"tenant_id": str(tenant_id), "tenant_slug": slug}},
    }
    if email:
        params["customer_email"] = email

    session = stripe.checkout.Session.create(**params)
    return session.url


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request):
    payload   = await request.body()
    sig       = request.headers.get("stripe-signature", "")

    stripe = _stripe()

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        except stripe.error.SignatureVerificationError:
            raise HTTPException(400, "Invalid signature")
    else:
        import json
        event = json.loads(payload)

    etype = event["type"]

    if etype == "checkout.session.completed":
        _handle_checkout_completed(event["data"]["object"])

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        _handle_subscription_ended(event["data"]["object"])

    elif etype == "customer.subscription.resumed":
        _handle_subscription_resumed(event["data"]["object"])

    return {"received": True}


def _handle_checkout_completed(session: dict):
    tenant_id   = session.get("metadata", {}).get("tenant_id")
    customer_id = session.get("customer")
    sub_id      = session.get("subscription")

    if not tenant_id:
        return

    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                UPDATE sinergy_tenants
                SET status = 'active',
                    stripe_customer_id = :cid,
                    stripe_subscription_id = :sid
                WHERE id = :id
                RETURNING slug, name, plan, email, api_key
            """),
            {"cid": customer_id, "sid": sub_id, "id": int(tenant_id)},
        ).fetchone()
        db.commit()

        if row and row.email:
            _send_api_key_email(row.name, row.email, row.api_key, row.plan)
    finally:
        db.close()


def _handle_subscription_ended(subscription: dict):
    sub_id = subscription.get("id")
    if not sub_id:
        return
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE sinergy_tenants SET status = 'suspended' WHERE stripe_subscription_id = :sid"),
            {"sid": sub_id},
        )
        db.commit()
    finally:
        db.close()


def _handle_subscription_resumed(subscription: dict):
    sub_id = subscription.get("id")
    if not sub_id:
        return
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE sinergy_tenants SET status = 'active' WHERE stripe_subscription_id = :sid"),
            {"sid": sub_id},
        )
        db.commit()
    finally:
        db.close()


def _send_api_key_email(name: str, email: str, api_key: str, plan: str):
    from alert_service import _send
    subject = "🔑 Tu API key de SinergyOS está lista"
    html = f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;background:#0f0f0f;color:#e5e5e5;border-radius:12px;overflow:hidden">
  <div style="background:#7c3aed;padding:24px 32px">
    <h1 style="margin:0;font-size:20px;color:#fff">¡Bienvenido a SinergyOS!</h1>
  </div>
  <div style="padding:32px">
    <p style="margin:0 0 16px">Hola <strong>{name}</strong>,</p>
    <p style="margin:0 0 24px;color:#a3a3a3">
      Tu pago fue procesado. Aquí está tu API key — guárdala en un lugar seguro,
      no se puede recuperar después.
    </p>
    <div style="background:#0a0a0a;border:1px solid #2a2a2a;border-radius:8px;padding:16px;margin-bottom:24px">
      <p style="margin:0 0 8px;font-size:11px;color:#525252;text-transform:uppercase;letter-spacing:.05em">Tu API Key</p>
      <code style="color:#34d399;font-size:13px;word-break:break-all">{api_key}</code>
    </div>
    <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:16px;margin-bottom:24px;font-family:monospace;font-size:12px">
      <p style="margin:0 0 4px;color:#525252"># Prueba tu integración</p>
      <p style="margin:0;color:#a3a3a3">curl -X POST https://sinergy.io/chat/ask \\</p>
      <p style="margin:0;color:#a3a3a3;padding-left:16px">-H "X-API-Key: {api_key[:20]}..." \\</p>
      <p style="margin:0;color:#a3a3a3;padding-left:16px">-d '{{"pregunta":"¿Qué puedes hacer?"}}'</p>
    </div>
    <p style="margin:0;color:#525252;font-size:13px">Plan: <strong style="color:#e5e5e5;text-transform:capitalize">{plan}</strong></p>
  </div>
  <div style="padding:16px 32px;border-top:1px solid #1a1a1a;font-size:11px;color:#525252">
    SinergyOS — Agentes IA para tu empresa · <a href="mailto:agarza@bion-business.com" style="color:#7c3aed">Soporte</a>
  </div>
</div>"""
    _send(email, subject, html)
