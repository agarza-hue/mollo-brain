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
                SELECT id, slug, name, plan, req_used, req_limit, is_admin
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
    """Incrementa req_used en 1. Llamar en background tras cada request exitoso."""
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE sinergy_tenants SET req_used = req_used + 1 WHERE id = :id"),
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
