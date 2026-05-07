"""
InsForge Tenants — gestión de tenants de SinergyOS.
Endpoints bajo /sinergy/tenants (sin auth por ahora, agregar en prod).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from db import SessionLocal
from insforge import generate_api_key, PLAN_LIMITS

router = APIRouter(prefix="/sinergy", tags=["InsForge"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    slug: str
    name: str
    plan: str = "basic"


class TenantOut(BaseModel):
    id: int
    slug: str
    name: str
    plan: str
    req_used: int
    req_limit: int
    api_key: Optional[str]
    created_at: datetime
    usage_pct: float
    is_admin: bool = False


class TenantSummary(BaseModel):
    id: int
    slug: str
    name: str
    plan: str
    req_used: int
    req_limit: int
    usage_pct: float
    is_admin: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_out(row: dict) -> dict:
    limit = row["req_limit"]
    used  = row["req_used"] or 0
    pct   = round(used / limit * 100, 1) if limit < 999_999 else 0.0
    return {**row, "usage_pct": pct, "is_admin": bool(row.get("is_admin", False))}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/tenants", response_model=list[TenantSummary])
def list_tenants():
    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT id, slug, name, plan, req_used, req_limit, is_admin FROM sinergy_tenants ORDER BY id")
        ).fetchall()
        return [_row_to_out(dict(r._mapping)) for r in rows]
    finally:
        db.close()


@router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreate):
    if body.plan not in PLAN_LIMITS:
        raise HTTPException(400, f"Plan inválido. Opciones: {list(PLAN_LIMITS)}")

    key   = generate_api_key()
    limit = PLAN_LIMITS[body.plan]

    db = SessionLocal()
    try:
        existing = db.execute(
            text("SELECT id FROM sinergy_tenants WHERE slug = :slug"),
            {"slug": body.slug},
        ).fetchone()
        if existing:
            raise HTTPException(409, f"El slug '{body.slug}' ya existe")

        row = db.execute(
            text("""
                INSERT INTO sinergy_tenants (slug, name, plan, req_limit, api_key)
                VALUES (:slug, :name, :plan, :limit, :key)
                RETURNING id, slug, name, plan, req_used, req_limit, api_key, created_at, is_admin
            """),
            {"slug": body.slug, "name": body.name, "plan": body.plan,
             "limit": limit, "key": key},
        ).fetchone()
        db.commit()
        return _row_to_out(dict(row._mapping))
    finally:
        db.close()


@router.get("/tenants/{slug}", response_model=TenantOut)
def get_tenant(slug: str):
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT id, slug, name, plan, req_used, req_limit, api_key, created_at, is_admin FROM sinergy_tenants WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Tenant '{slug}' no encontrado")
        return _row_to_out(dict(row._mapping))
    finally:
        db.close()


@router.post("/tenants/{slug}/toggle-admin", response_model=TenantOut)
def toggle_admin(slug: str):
    """Promueve o degrada un tenant a admin. El tenant 'adolfo' no puede perder is_admin."""
    db = SessionLocal()
    try:
        current = db.execute(
            text("SELECT is_admin FROM sinergy_tenants WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
        if not current:
            raise HTTPException(404, f"Tenant '{slug}' no encontrado")
        if slug == "adolfo" and bool(current[0]):
            raise HTTPException(403, "No se puede revocar el admin al tenant fundador")
        new_val = not bool(current[0])
        row = db.execute(
            text("""
                UPDATE sinergy_tenants SET is_admin = :val WHERE slug = :slug
                RETURNING id, slug, name, plan, req_used, req_limit, api_key, created_at, is_admin, is_admin
            """),
            {"val": new_val, "slug": slug},
        ).fetchone()
        db.commit()
        return _row_to_out(dict(row._mapping))
    finally:
        db.close()


@router.post("/tenants/{slug}/rotate-key", response_model=TenantOut)
def rotate_api_key(slug: str):
    """Genera una nueva API key e invalida la anterior."""
    new_key = generate_api_key()
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                UPDATE sinergy_tenants SET api_key = :key WHERE slug = :slug
                RETURNING id, slug, name, plan, req_used, req_limit, api_key, created_at, is_admin
            """),
            {"key": new_key, "slug": slug},
        ).fetchone()
        db.commit()
        if not row:
            raise HTTPException(404, f"Tenant '{slug}' no encontrado")
        return _row_to_out(dict(row._mapping))
    finally:
        db.close()


@router.post("/tenants/{slug}/reset-usage", response_model=TenantOut)
def reset_usage(slug: str):
    """Resetea req_used a 0 (para renovación mensual)."""
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                UPDATE sinergy_tenants SET req_used = 0 WHERE slug = :slug
                RETURNING id, slug, name, plan, req_used, req_limit, api_key, created_at, is_admin
            """),
            {"slug": slug},
        ).fetchone()
        db.commit()
        if not row:
            raise HTTPException(404, f"Tenant '{slug}' no encontrado")
        return _row_to_out(dict(row._mapping))
    finally:
        db.close()


@router.delete("/tenants/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(slug: str):
    if slug == "adolfo":
        raise HTTPException(403, "No se puede eliminar el tenant fundador")
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM sinergy_tenants WHERE slug = :slug"),
            {"slug": slug},
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, f"Tenant '{slug}' no encontrado")
    finally:
        db.close()
