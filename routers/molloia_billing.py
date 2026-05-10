"""
MolloIA — landing/app checkout via Stripe.

Distinct from `routers/billing.py` (SinergyOS multi-tenant) — this one is
for the public mollo-ai.com landing where individuals subscribe to Pro/Team.

Flow:
  1. Visitor clicks "Suscribirme" on landing.mollo-ai.com.
  2. Browser hits GET /billing/checkout?plan=pro (302 redirect).
  3. We create a Stripe Checkout Session and 302 to its URL.
  4. Stripe collects email + card.
  5. On success, Stripe redirects to /billing/success?session_id=<sid>.
  6. On cancel, Stripe redirects to /billing/cancel.

No auth required for checkout creation — Stripe collects the email itself.
After success we encourage the user to create their MolloIA account
pre-filled with the email captured at Stripe.

Webhook (POST /billing/webhook) is intentionally minimal here; it acks
events but doesn't yet provision accounts. Add provisioning logic in a
follow-up — for now the success page handles user-visible feedback.
"""
import json as _json
import os
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from db import engine


router = APIRouter(prefix="/billing", tags=["MolloIA Billing"])

# ── Schema ───────────────────────────────────────────────────────────────────
# Subscriptions table tracks Stripe state. user_id is nullable: we may receive
# a webhook for someone who hasn't registered yet — the row sits orphaned with
# their email until they sign up, at which point register() links the row.
with engine.connect() as _conn:
    _conn.execute(text("""
        CREATE TABLE IF NOT EXISTS molloia_subscriptions (
            id                     UUID PRIMARY KEY,
            user_id                UUID REFERENCES users(id) ON DELETE SET NULL,
            email                  TEXT NOT NULL,
            stripe_customer_id     TEXT NOT NULL,
            stripe_subscription_id TEXT UNIQUE NOT NULL,
            plan                   TEXT NOT NULL,
            status                 TEXT NOT NULL,
            quantity               INTEGER DEFAULT 1,
            current_period_end     TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    _conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_molloia_subs_email ON molloia_subscriptions (email)"
    ))
    _conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_molloia_subs_user ON molloia_subscriptions (user_id) WHERE user_id IS NOT NULL"
    ))
    _conn.commit()

SECRET_KEY      = os.getenv("MOLLOIA_STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET  = os.getenv("MOLLOIA_STRIPE_WEBHOOK_SECRET", "")
BASE_URL        = os.getenv("MOLLOIA_BASE_URL", "https://app.mollo-ai.com")
PRICE_IDS = {
    "pro":  os.getenv("MOLLOIA_STRIPE_PRICE_PRO",  ""),
    "team": os.getenv("MOLLOIA_STRIPE_PRICE_TEAM", ""),
}


def _stripe():
    import stripe
    if not SECRET_KEY:
        raise HTTPException(503, "Stripe not configured (MOLLOIA_STRIPE_SECRET_KEY missing)")
    stripe.api_key = SECRET_KEY
    return stripe


@router.get("/checkout")
def create_checkout(
    plan: Literal["pro", "team"] = Query(...),
    quantity: int = Query(1, ge=1, le=200),
    email: str | None = Query(None),
):
    """Create a Stripe Checkout Session and 302 to its URL."""
    price_id = PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(400, f"Plan no configurado: {plan}")

    stripe = _stripe()
    # Team has minimum 3 seats (advertised on landing); enforce here.
    if plan == "team" and quantity < 3:
        quantity = 3

    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": quantity}],
        "success_url": f"{BASE_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url":  f"{BASE_URL}/billing/cancel?plan={plan}",
        "allow_promotion_codes": True,
        "billing_address_collection": "auto",
        "metadata": {"product": "molloia", "plan": plan},
        "subscription_data": {"metadata": {"product": "molloia", "plan": plan}},
    }
    if email:
        params["customer_email"] = email

    session = stripe.checkout.Session.create(**params)
    return RedirectResponse(session.url, status_code=303)


@router.get("/success", response_class=HTMLResponse)
def success(session_id: str = Query(...)):
    """Confirmation page rendered after Stripe redirects back."""
    stripe = _stripe()
    try:
        sess = stripe.checkout.Session.retrieve(
            session_id,
            expand=["customer", "subscription"],
        )
    except Exception as e:
        raise HTTPException(404, f"Session not found: {e}")

    # Stripe SDK v15: StripeObject no soporta .get() — re-parsear a dict plano.
    import json as _json
    sess_d = _json.loads(str(sess))
    email = (sess_d.get("customer_details") or {}).get("email") or ""
    plan  = (sess_d.get("metadata") or {}).get("plan") or "pro"
    register_url = f"{BASE_URL}/register?email={email}&plan={plan}"

    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pago recibido — MolloIA</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..600&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{ --paper:#F4F0E9; --ink:#1A1915; --ink-soft:#3D3B36; --coral:#D97757; --coral-deep:#B85B3F; --line:rgba(26,25,21,0.10); }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; background:var(--paper); color:var(--ink); font-family:'Inter',system-ui,sans-serif; display:grid; place-items:center; padding:32px; }}
  .card {{ max-width:520px; width:100%; background:#fff; border:1px solid var(--line); border-radius:16px; padding:40px 36px; box-shadow:0 12px 32px -16px rgba(26,25,21,0.18); }}
  .check {{ width:54px; height:54px; border-radius:50%; background:var(--coral); color:#fff; display:grid; place-items:center; margin-bottom:22px; font-size:26px; }}
  h1 {{ font-family:'Fraunces',Georgia,serif; font-weight:500; font-size:30px; line-height:1.15; margin:0 0 10px; letter-spacing:-0.02em; }}
  p {{ color:var(--ink-soft); line-height:1.55; font-size:15px; margin:0 0 16px; }}
  .meta {{ background:rgba(26,25,21,0.04); border-radius:10px; padding:14px 16px; font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--ink-soft); margin:18px 0 26px; }}
  .meta b {{ color:var(--ink); font-weight:500; }}
  .cta {{ display:inline-flex; align-items:center; gap:8px; padding:13px 22px; background:var(--coral); color:#fff; border-radius:999px; text-decoration:none; font-weight:500; font-size:15px; transition:background 0.2s; }}
  .cta:hover {{ background:var(--coral-deep); }}
  .small {{ display:block; margin-top:18px; color:rgba(26,25,21,0.5); font-size:12.5px; }}
</style></head><body>
<div class="card">
  <div class="check">✓</div>
  <h1>Pago recibido. <em style="color:var(--coral)">Bienvenido</em>.</h1>
  <p>Tu suscripción al plan <b>{plan.title()}</b> está activa. Solo nos falta crear tu cuenta para que entres al dashboard.</p>
  <div class="meta">
    plan: <b>{plan}</b><br>
    email: <b>{email or '—'}</b><br>
    session: <b>{session_id[:24]}…</b>
  </div>
  <a class="cta" href="{register_url}">Crear mi cuenta →</a>
  <span class="small">Si ya tienes cuenta, <a href="{BASE_URL}/login?email={email}" style="color:var(--coral); text-decoration:none;">inicia sesión</a> y vincularemos tu suscripción al cargar.</span>
</div>
</body></html>"""
    return html


@router.get("/cancel", response_class=HTMLResponse)
def cancel(plan: str = Query("pro")):
    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pago cancelado — MolloIA</title>
<style>
  body {{ margin:0; min-height:100vh; background:#F4F0E9; color:#1A1915; font-family:system-ui; display:grid; place-items:center; padding:32px; }}
  .card {{ max-width:480px; text-align:center; background:#fff; padding:36px 32px; border-radius:14px; border:1px solid rgba(26,25,21,0.10); }}
  h1 {{ font-size:22px; margin:0 0 8px; }}
  p {{ color:#3D3B36; line-height:1.55; }}
  a {{ color:#D97757; text-decoration:none; font-weight:500; }}
</style></head><body>
<div class="card">
  <h1>Sin cargo</h1>
  <p>Cerraste el checkout. No se cobró nada.</p>
  <p><a href="https://landing.mollo-ai.com/#precios">← Ver planes de nuevo</a></p>
</div>
</body></html>"""
    return html


def _upsert_subscription(
    *, email: str, plan: str, customer_id: str, subscription_id: str,
    status: str, quantity: int, period_end: Optional[int],
):
    """Create or update a subscription row, linking to user if email matches."""
    import uuid
    period_end_ts = (
        datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()
        if period_end else None
    )
    with engine.begin() as conn:
        # Check for existing user by email
        urow = conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).fetchone()
        user_id = str(urow.id) if urow else None

        # Upsert by stripe_subscription_id
        conn.execute(text("""
            INSERT INTO molloia_subscriptions
                (id, user_id, email, stripe_customer_id, stripe_subscription_id,
                 plan, status, quantity, current_period_end, updated_at)
            VALUES (:id, :uid, :email, :cust, :sub, :plan, :status, :qty, :end, NOW())
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                user_id = COALESCE(molloia_subscriptions.user_id, EXCLUDED.user_id),
                email   = EXCLUDED.email,
                plan    = EXCLUDED.plan,
                status  = EXCLUDED.status,
                quantity = EXCLUDED.quantity,
                current_period_end = EXCLUDED.current_period_end,
                updated_at = NOW()
        """), {
            "id": str(uuid.uuid4()), "uid": user_id, "email": email,
            "cust": customer_id, "sub": subscription_id,
            "plan": plan, "status": status, "qty": quantity,
            "end": period_end_ts,
        })

        # If we have a user, also reflect the plan on the user record
        # (only "upgrade" — never auto-downgrade from active subscription event).
        if user_id and status == "active" and plan in ("pro", "team"):
            conn.execute(
                text("UPDATE users SET plan = :p WHERE id = :id"),
                {"p": plan, "id": user_id},
            )


def _mark_subscription_cancelled(subscription_id: str):
    """On subscription deletion, mark cancelled and revert user plan to free."""
    with engine.begin() as conn:
        row = conn.execute(text(
            "UPDATE molloia_subscriptions SET status = 'cancelled', updated_at = NOW() "
            "WHERE stripe_subscription_id = :sub RETURNING user_id"
        ), {"sub": subscription_id}).fetchone()
        if row and row.user_id:
            conn.execute(
                text("UPDATE users SET plan = 'free' WHERE id = :id"),
                {"id": str(row.user_id)},
            )


@router.post("/webhook")
@router.post("/webhook/")
async def webhook(request: Request):
    """Stripe webhook — auto-provision subscriptions in our DB on payment.

    Events handled:
      - checkout.session.completed   → create/update subscription row
      - customer.subscription.updated → keep state in sync
      - customer.subscription.deleted → cancel + revert user plan to free
      - invoice.payment_failed       → flag past_due (no auto-downgrade yet)

    Stripe SDK v15 returns StripeObjects from construct_event; we re-parse
    the raw payload to a plain dict (project convention from CLAUDE.md).
    """
    if not WEBHOOK_SECRET:
        return {"status": "skipped", "reason": "webhook secret not configured"}

    stripe = _stripe()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(400, f"Invalid signature: {e}")

    raw = _json.loads(payload.decode("utf-8"))
    etype = raw.get("type")
    obj = raw.get("data", {}).get("object", {})

    if etype == "checkout.session.completed":
        # `obj` here is the raw dict from the JSON payload — safe with .get().
        # When we call Stripe, the SDK returns StripeObject (no .get); we cast
        # to dict via .to_dict_recursive() for uniform access.
        sub_id = obj.get("subscription")
        if not sub_id:
            return {"status": "ignored", "reason": "non-subscription session"}

        sub_obj = stripe.Subscription.retrieve(sub_id, expand=["customer"])
        # StripeObject → plain dict via JSON round-trip (works across SDK versions).
        sub = _json.loads(str(sub_obj))

        item = sub["items"]["data"][0]
        plan = (sub.get("metadata") or {}).get("plan") \
            or (obj.get("metadata") or {}).get("plan") \
            or "pro"
        cust = sub.get("customer") or {}
        email = (
            (obj.get("customer_details") or {}).get("email")
            or obj.get("customer_email")
            or (cust.get("email") if isinstance(cust, dict) else None)
            or ""
        )
        customer_id = cust["id"] if isinstance(cust, dict) else str(cust)
        quantity = int(item.get("quantity", 1))
        _upsert_subscription(
            email=email,
            plan=plan,
            customer_id=customer_id,
            subscription_id=sub["id"],
            status=sub["status"],
            quantity=quantity,
            period_end=sub.get("current_period_end"),
        )

        # Send welcome email — non-blocking (failures shouldn't break webhook).
        try:
            from alert_service import send_molloia_welcome
            register_url = f"{BASE_URL}/register?email={email}&plan={plan}"
            amount_total = obj.get("amount_total")  # cents
            send_molloia_welcome(
                email=email,
                plan=plan,
                register_url=register_url,
                amount_paid_usd=(amount_total / 100) if amount_total else None,
                quantity=quantity,
            )
        except Exception as e:
            print(f"[molloia_billing] welcome email failed for {email}: {e}")

        return {"status": "ok", "action": "provisioned", "email": email, "plan": plan}

    if etype == "customer.subscription.updated":
        item = obj["items"]["data"][0]
        plan = (obj.get("metadata") or {}).get("plan") or "pro"
        cust_obj = stripe.Customer.retrieve(obj["customer"])
        cust = _json.loads(str(cust_obj))
        _upsert_subscription(
            email=cust.get("email", "") or "",
            plan=plan,
            customer_id=obj["customer"],
            subscription_id=obj["id"],
            status=obj["status"],
            quantity=int(item.get("quantity", 1)),
            period_end=obj.get("current_period_end"),
        )
        return {"status": "ok", "action": "updated"}

    if etype == "customer.subscription.deleted":
        _mark_subscription_cancelled(obj["id"])
        return {"status": "ok", "action": "cancelled"}

    if etype == "invoice.payment_failed":
        sub_id = obj.get("subscription")
        if sub_id:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE molloia_subscriptions SET status = 'past_due', updated_at = NOW() "
                    "WHERE stripe_subscription_id = :sub"
                ), {"sub": sub_id})
        return {"status": "ok", "action": "flagged_past_due"}

    # Other events we receive but don't act on (yet).
    return {"status": "ok", "action": "noop", "type": etype}
