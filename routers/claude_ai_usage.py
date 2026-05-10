"""Endpoints para snapshots de uso de Claude.ai (lectura manual del Settings).

No hay API pública para pullar el % consumido en claude.ai web; el usuario lo
copia de Settings → Usage cada cierto tiempo. Aquí lo persistimos para que el
dashboard lea el valor más reciente sin tocar código.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional
from sqlalchemy import text

from db import engine

router = APIRouter(prefix="/claude_ai_usage", tags=["Claude.ai Usage"])


class UsageSnapshotIn(BaseModel):
    period_type: Literal["daily", "weekly", "monthly"]
    usage_pct:   float = Field(..., ge=0, le=200)
    reset_label: Optional[str] = None
    notes:       Optional[str] = None


@router.get("/latest")
def latest():
    """Snapshot más reciente por cada period_type. None si no hay registros."""
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT ON (period_type)
                period_type, usage_pct, reset_label, notes, read_at, created_at
            FROM claude_ai_usage_snapshots
            ORDER BY period_type, read_at DESC
        """)).mappings().all()
    out = {"daily": None, "weekly": None, "monthly": None}
    for r in rows:
        out[r["period_type"]] = {
            "usage_pct":   float(r["usage_pct"]),
            "reset_label": r["reset_label"],
            "notes":       r["notes"],
            "read_at":     r["read_at"].isoformat(),
        }
    return out


@router.get("/history")
def history(period_type: Optional[str] = None, limit: int = 50):
    sql = """
        SELECT id, period_type, usage_pct, reset_label, notes, read_at
        FROM claude_ai_usage_snapshots
    """
    params: dict = {"limit": limit}
    if period_type:
        sql += " WHERE period_type = :pt"
        params["pt"] = period_type
    sql += " ORDER BY read_at DESC LIMIT :limit"
    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r, id=str(r["id"]), read_at=r["read_at"].isoformat()) for r in rows]


@router.post("")
def insert_snapshot(snap: UsageSnapshotIn):
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO claude_ai_usage_snapshots (period_type, usage_pct, reset_label, notes)
            VALUES (:pt, :pct, :label, :notes)
            RETURNING id, read_at
        """), {
            "pt":    snap.period_type,
            "pct":   snap.usage_pct,
            "label": snap.reset_label,
            "notes": snap.notes,
        }).mappings().first()
    return {
        "status":  "ok",
        "id":      str(row["id"]),
        "read_at": row["read_at"].isoformat(),
    }
