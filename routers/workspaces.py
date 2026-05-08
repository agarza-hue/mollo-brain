"""Workspaces — agrupación lógica de conversaciones por proyecto / contexto.

Cada usuario tiene su lista de workspaces. Una conversación pertenece a
0 o 1 workspace (column `workspace_id` en `conversations`).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Any

from db import get_db, engine
from auth import get_current_user

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


# ── Tabla (idempotente) ───────────────────────────────────────────────────────

with engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT,
            branch      TEXT,
            hue         INTEGER,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_workspaces_user "
        "ON workspaces(user_id, updated_at DESC)"
    ))
    conn.commit()


def _row(r) -> dict:
    return dict(r._mapping)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_workspaces(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Lista los workspaces del usuario, con un conteo de conversaciones asignadas."""
    rows = db.execute(text("""
        SELECT w.id, w.name, w.description, w.branch, w.hue,
               w.created_at, w.updated_at,
               COUNT(c.id) AS conv_count
        FROM workspaces w
        LEFT JOIN conversations c
          ON c.workspace_id = w.id AND c.user_id = w.user_id
        WHERE w.user_id = :uid
        GROUP BY w.id
        ORDER BY w.updated_at DESC
    """), {"uid": str(user["id"])}).fetchall()
    return [_row(r) for r in rows]


@router.post("", status_code=201)
def create_workspace(
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Crea un workspace. El cliente provee `id` (UUID) — mismo patrón que /convs."""
    wid = body.get("id")
    name = body.get("name")
    if not wid:
        raise HTTPException(400, "id requerido")
    if not name:
        raise HTTPException(400, "name requerido")

    db.execute(text("""
        INSERT INTO workspaces (id, user_id, name, description, branch, hue)
        VALUES (:id, :uid, :name, :description, :branch, :hue)
        ON CONFLICT (id) DO NOTHING
    """), {
        "id":          wid,
        "uid":         str(user["id"]),
        "name":        name,
        "description": body.get("description"),
        "branch":      body.get("branch"),
        "hue":         body.get("hue"),
    })
    db.commit()
    return {"id": wid, "name": name}


@router.patch("/{wid}")
def update_workspace(
    wid: str,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Actualiza name/description/branch/hue. Campos omitidos no se tocan."""
    sets: list[str] = []
    params: dict[str, Any] = {"id": wid, "uid": str(user["id"])}

    for key in ("name", "description", "branch", "hue"):
        if key in body:
            sets.append(f"{key} = :{key}")
            params[key] = body[key]

    if not sets:
        return {"ok": True}

    sets.append("updated_at = NOW()")
    db.execute(
        text(f"""
            UPDATE workspaces
            SET {", ".join(sets)}
            WHERE id = :id AND user_id = :uid
        """),
        params,
    )
    db.commit()
    return {"ok": True}


@router.delete("/{wid}", status_code=204)
def delete_workspace(
    wid: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Borra el workspace. Las convs asignadas quedan con workspace_id = NULL
    (las desvinculamos manualmente porque no usamos FK con ON DELETE)."""
    db.execute(text("""
        UPDATE conversations
        SET workspace_id = NULL, updated_at = NOW()
        WHERE workspace_id = :id AND user_id = :uid
    """), {"id": wid, "uid": str(user["id"])})
    db.execute(text("""
        DELETE FROM workspaces WHERE id = :id AND user_id = :uid
    """), {"id": wid, "uid": str(user["id"])})
    db.commit()
