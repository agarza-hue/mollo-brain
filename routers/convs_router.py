"""Historial de conversaciones por usuario — persistido en Postgres."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Any

from db import get_db
from auth import get_current_user

router = APIRouter(prefix="/convs", tags=["Conversaciones"])

# ── Tablas (se crean al importar) ─────────────────────────────────────────────

from db import engine

with engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS conversations (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            title      TEXT NOT NULL DEFAULT 'Nueva conversación',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS conv_messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL DEFAULT '',
            model           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_convs_user ON conversations(user_id, updated_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_msgs_conv  ON conv_messages(conversation_id, created_at)"
    ))
    conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(r) -> dict:
    return dict(r._mapping)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_convs(
    limit: int = 40,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Lista las últimas N conversaciones con sus mensajes."""
    convs = db.execute(text("""
        SELECT id, title, created_at, updated_at
        FROM conversations
        WHERE user_id = :uid
        ORDER BY updated_at DESC
        LIMIT :lim
    """), {"uid": str(user["id"]), "lim": limit}).fetchall()

    result = []
    for c in convs:
        msgs = db.execute(text("""
            SELECT id, role, content, model, created_at
            FROM conv_messages
            WHERE conversation_id = :cid
            ORDER BY created_at
        """), {"cid": c.id}).fetchall()
        result.append({
            **_row(c),
            "messages": [_row(m) for m in msgs],
        })
    return result


@router.post("", status_code=201)
def create_conv(
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    cid   = body.get("id")
    title = body.get("title", "Nueva conversación")
    if not cid:
        raise HTTPException(400, "id requerido")

    db.execute(text("""
        INSERT INTO conversations (id, user_id, title)
        VALUES (:id, :uid, :title)
        ON CONFLICT (id) DO NOTHING
    """), {"id": cid, "uid": str(user["id"]), "title": title})
    db.commit()
    return {"id": cid, "title": title}


@router.patch("/{cid}")
def update_conv(
    cid: str,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    title = body.get("title")
    if title:
        db.execute(text("""
            UPDATE conversations
            SET title = :title, updated_at = NOW()
            WHERE id = :id AND user_id = :uid
        """), {"title": title, "id": cid, "uid": str(user["id"])})
        db.commit()
    return {"ok": True}


@router.delete("/{cid}", status_code=204)
def delete_conv(
    cid: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    db.execute(text("""
        DELETE FROM conversations WHERE id = :id AND user_id = :uid
    """), {"id": cid, "uid": str(user["id"])})
    db.commit()


@router.post("/{cid}/messages")
def save_messages(
    cid: str,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upsert de mensajes. El frontend envía el par user+assistant tras cada respuesta."""
    msgs: list[dict[str, Any]] = body.get("messages", [])
    if not msgs:
        return {"saved": 0}

    # Verificar que la conversación pertenece al usuario
    row = db.execute(text(
        "SELECT id FROM conversations WHERE id = :id AND user_id = :uid"
    ), {"id": cid, "uid": str(user["id"])}).fetchone()
    if not row:
        raise HTTPException(404, "Conversación no encontrada")

    for m in msgs:
        db.execute(text("""
            INSERT INTO conv_messages (id, conversation_id, role, content, model)
            VALUES (:id, :cid, :role, :content, :model)
            ON CONFLICT (id) DO UPDATE
              SET content = EXCLUDED.content,
                  model   = EXCLUDED.model
        """), {
            "id":      m["id"],
            "cid":     cid,
            "role":    m["role"],
            "content": m.get("content", ""),
            "model":   m.get("model"),
        })

    db.execute(text(
        "UPDATE conversations SET updated_at = NOW() WHERE id = :id"
    ), {"id": cid})
    db.commit()
    return {"saved": len(msgs)}
