"""
InsForge — capa multi-tenant de SinergyOS.

Valida X-API-Key contra sinergy_tenants, verifica el límite del plan
e incrementa req_used después de cada request exitoso.
"""
import secrets
from typing import Optional

from fastapi import Header, HTTPException, status
from sqlalchemy import text

from db import SessionLocal


# ── Dependency ────────────────────────────────────────────────────────────────

def get_tenant(x_api_key: Optional[str] = Header(default=None)) -> Optional[dict]:
    """
    FastAPI dependency — opcional.
    Si no hay header → devuelve None (el endpoint usa auth JWT normal).
    Si hay header inválido → 401.
    Si límite superado → 429.
    Si OK → devuelve dict del tenant.
    """
    if not x_api_key:
        return None

    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT id, slug, name, plan, req_used, req_limit, is_admin, status
                FROM sinergy_tenants
                WHERE api_key = :key
            """),
            {"key": x_api_key},
        ).fetchone()
    finally:
        db.close()

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="API key inválida")

    tenant = dict(row._mapping)

    if tenant.get("status") == "suspended":
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail="Cuenta suspendida. Verifica tu suscripción.")

    if tenant.get("status") == "pending":
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail="Cuenta pendiente de pago. Completa el checkout.")

    # Admin siempre pasa — sin restricciones de ningún tipo
    if not tenant.get("is_admin"):
        if tenant["req_limit"] < 999999 and tenant["req_used"] >= tenant["req_limit"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Límite del plan alcanzado ({tenant['req_limit']:,} requests/mes). "
                       f"Usados: {tenant['req_used']:,}.",
            )

    return tenant


def increment_usage(tenant_id: int) -> None:
    """Incrementa req_used en 1 y dispara alertas de uso en umbrales 80% y 100%."""
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE sinergy_tenants SET req_used = req_used + 1 WHERE id = :id"),
            {"id": tenant_id},
        )
        db.commit()

        row = db.execute(
            text("""
                SELECT slug, name, plan, email, req_used, req_limit,
                       alert_sent_80, is_admin
                FROM sinergy_tenants WHERE id = :id
            """),
            {"id": tenant_id},
        ).fetchone()
        if not row:
            return

        t = dict(row._mapping)
        if t["is_admin"] or not t["email"] or t["req_limit"] >= 999_999:
            return

        used, limit = t["req_used"], t["req_limit"]
        pct = used / limit

        if pct >= 0.80 and not t["alert_sent_80"]:
            from alert_service import send_usage_alert_80, send_limit_reached
            if pct >= 1.0:
                send_limit_reached(t["name"], t["email"], t["plan"], limit, t["slug"])
            else:
                send_usage_alert_80(t["name"], t["email"], t["plan"], used, limit, t["slug"])
            db.execute(
                text("UPDATE sinergy_tenants SET alert_sent_80 = TRUE WHERE id = :id"),
                {"id": tenant_id},
            )
            db.commit()
    finally:
        db.close()


# ── Utilidades de gestión ─────────────────────────────────────────────────────

def generate_api_key() -> str:
    """Genera una API key con prefijo 'sk-sy-' y 32 bytes aleatorios."""
    return f"sk-sy-{secrets.token_urlsafe(32)}"


PLAN_LIMITS = {
    "basic":      500,
    "pro":        5_000,
    "enterprise": 999_999,
}
