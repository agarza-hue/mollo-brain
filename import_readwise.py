"""
Importa Readwise highlights + Reader documents → Qdrant RAG.

Usa el CLI oficial `readwise` (npm @readwise/cli) que ya está autenticado
con login-with-token. El CLI maneja auth, refresh, retry — Mollo solo
ejecuta subprocess y parsea JSON.

Estrategia de sync:
  - Reader documents: archivo más rico (con `content` markdown completo).
    Indexa como chunks por documento. Polling delta vía updated_at-gt
  - Readwise highlights: snippets cortos (≤500 chars típicamente).
    Indexa cada highlight como 1 chunk. Polling delta vía highlighted-at-gt

Dedup: SQLite local ~/.mollo/readwise_dedup.sqlite, key = readwise ID,
incluye SHA1 del contenido para detectar updates.

Categoria en Qdrant: 'readwise'. Subtype distingue highlight vs document
en payload.

Uso:
    /root/venv/bin/python -m import_readwise
"""
import os
import sys
import json
import hashlib
import logging
import sqlite3
import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv("/root/mollo_brain/.env")

from document_service import _chunk_text
from qdrant_service import upsert_vectors, client as qclient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue
from embeddings import get_embedding
from config import QDRANT_COLLECTION
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("readwise_import")

DEDUP_DB    = os.path.expanduser("~/.mollo/readwise_dedup.sqlite")
CLI         = "readwise"
PAGE_SIZE   = 50          # límite por página al CLI
MAX_PAGES   = 20          # cap de páginas por run para no atorarse

# Limit content size por chunk para que embedding no truene
MAX_DOC_CHARS = 50_000    # cap docs muy largos


def _ensure_db():
    os.makedirs(os.path.dirname(DEDUP_DB), exist_ok=True)
    con = sqlite3.connect(DEDUP_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS readwise_items (
            id          TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,            -- 'highlight' | 'document'
            sha1        TEXT NOT NULL,
            indexed_at  TEXT NOT NULL,
            chunks      INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS readwise_cursor (
            kind            TEXT PRIMARY KEY,     -- 'highlight' | 'document'
            last_sync_iso   TEXT NOT NULL
        )
    """)
    con.commit()
    return con


def _get_cursor(con, kind: str) -> str | None:
    row = con.execute("SELECT last_sync_iso FROM readwise_cursor WHERE kind = ?", (kind,)).fetchone()
    return row[0] if row else None


def _set_cursor(con, kind: str, iso: str):
    con.execute(
        "INSERT OR REPLACE INTO readwise_cursor(kind, last_sync_iso) VALUES (?, ?)",
        (kind, iso),
    )
    con.commit()


def _cli_json(*args) -> dict:
    """Ejecuta `readwise --json <args>` y parsea stdout JSON."""
    cmd = [CLI, "--json", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"CLI timeout: {' '.join(cmd)}")
    if r.returncode != 0:
        raise RuntimeError(f"CLI error ({r.returncode}): {r.stderr[:300]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"CLI returned invalid JSON: {e} | stdout: {r.stdout[:200]}")


# ── Highlights ─────────────────────────────────────────────────────────────────

def _fetch_highlights(since_iso: str | None) -> list[dict]:
    """Trae highlights paginados, opcionalmente delta desde since_iso."""
    args = ["readwise-list-highlights", "--page-size", str(PAGE_SIZE)]
    if since_iso:
        args += ["--highlighted-at-gt", since_iso]

    all_results = []
    page = 1
    next_cursor = None
    while page <= MAX_PAGES:
        page_args = list(args)
        if next_cursor:
            page_args += ["--page-cursor", next_cursor]
        data = _cli_json(*page_args)
        results = data.get("results", []) or []
        all_results.extend(results)
        next_cursor = data.get("nextPageCursor") or data.get("next")
        if not next_cursor or not results:
            break
        page += 1
    return all_results


def _build_highlight_chunk(h: dict) -> tuple[str, dict]:
    """De un highlight, devuelve (texto a embeddear, payload Qdrant)."""
    text     = h.get("text", "") or h.get("highlight_plaintext", "") or ""
    note     = h.get("note") or ""
    title    = h.get("book_title") or h.get("document_title") or "Sin título"
    author   = h.get("book_author") or h.get("document_author") or ""
    tags     = h.get("tags") or []
    source_url = h.get("url") or h.get("readable_url") or ""

    # Texto a embeddear: highlight + nota (la nota agrega tu propio contexto al chunk)
    chunk_text = text
    if note:
        chunk_text += f"\n\nNota: {note}"

    payload = {
        "source":      f"readwise://highlight/{h.get('id')}",
        "categoria":   "readwise",
        "subtype":     "highlight",
        "title":       title,
        "author":      author,
        "tags":        tags if isinstance(tags, list) else [],
        "url":         source_url,
        "highlighted_at": h.get("highlighted_at", ""),
        "text":        chunk_text,
        "chunk":       0,
        "total_chunks": 1,
    }
    return chunk_text, payload


# ── Reader documents ───────────────────────────────────────────────────────────

def _fetch_documents(since_iso: str | None) -> list[dict]:
    """Trae documentos del Reader. Usamos export delta para traer content
    completo (la lista normal no incluye `content` por default)."""
    # Lista por location y trae fields completos
    all_docs = []
    for loc in ("new", "later", "shortlist", "archive"):
        # Nota: `id` y `saved_at` NO se aceptan en --response-fields del CLI;
        # `id` viene siempre. Los demás según whitelist del CLI.
        args = ["reader-list-documents", "--location", loc, "--limit", "100",
                "--response-fields", "title,author,summary,content,url,source_url,"
                "category,tags,updated_at,word_count,site_name,created_at"]
        if since_iso:
            # No hay flag updated_at-gt en list-documents, filtramos client-side
            pass
        try:
            data = _cli_json(*args)
        except RuntimeError as e:
            logger.warning("fetch %s docs falló: %s", loc, e)
            continue
        for d in data.get("results", []):
            if since_iso and d.get("updated_at", "") <= since_iso:
                continue
            all_docs.append(d)
    return all_docs


def _build_document_chunks(d: dict) -> list[tuple[str, dict]]:
    """Chunkea el content de un Reader document."""
    title  = d.get("title", "") or "Sin título"
    author = d.get("author", "") or ""
    summary = d.get("summary", "") or ""
    content = d.get("content", "") or ""
    if not content.strip():
        # Si no hay content, indexa al menos summary como un chunk
        if summary.strip():
            content = summary
        else:
            return []

    # Cap docs muy largos para no spamear chunks
    if len(content) > MAX_DOC_CHARS:
        content = content[:MAX_DOC_CHARS] + "\n\n[...truncado]"

    chunks = _chunk_text(content)
    if not chunks:
        return []

    pairs = []
    for i, ch in enumerate(chunks):
        # Prepend title/author al primer chunk para mejorar recall semántico
        text = ch
        if i == 0:
            text = f"# {title}\n{('por ' + author) if author else ''}\n\n{ch}"
        payload = {
            "source":      f"readwise://document/{d.get('id')}",
            "categoria":   "readwise",
            "subtype":     "document",
            "title":       title,
            "author":      author,
            "tags":        d.get("tags") or [],
            "url":         d.get("source_url") or d.get("url") or "",
            "site_name":   d.get("site_name", ""),
            "saved_at":    d.get("saved_at", ""),
            "updated_at":  d.get("updated_at", ""),
            "text":        text,
            "chunk":       i,
            "total_chunks": len(chunks),
        }
        pairs.append((text, payload))
    return pairs


# ── Indexing ───────────────────────────────────────────────────────────────────

def _delete_old(item_id: str, kind: str):
    """Borra todos los chunks viejos de Qdrant para este id antes de re-indexar."""
    source = f"readwise://{kind}/{item_id}"
    try:
        qclient.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=Filter(must=[
                FieldCondition(key="source", match=MatchValue(value=source)),
            ]),
        )
    except Exception as e:
        logger.warning("delete viejo failed para %s: %s", source, e)


async def _index_pairs(pairs: list[tuple[str, dict]]):
    """Toma lista de (text, payload), embeddea, upserta a Qdrant."""
    if not pairs:
        return
    records = []
    for text, payload in pairs:
        records.append({
            "id":      str(uuid.uuid4()),
            "text":    text,
            "payload": payload,
        })
    embeddings = [await get_embedding(r["text"]) for r in records]
    upsert_vectors(records, embeddings, collection=QDRANT_COLLECTION)


# ── Main ───────────────────────────────────────────────────────────────────────

async def run_import() -> dict:
    logger.info("=== readwise import start ===")
    con = _ensure_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    stats = {"highlights_new": 0, "highlights_skipped": 0,
             "docs_new": 0, "docs_skipped": 0, "errors": 0}

    # ── Highlights ──
    hl_cursor = _get_cursor(con, "highlight")
    try:
        highlights = _fetch_highlights(hl_cursor)
        logger.info("Readwise highlights fetched: %d (delta desde %s)",
                    len(highlights), hl_cursor or "inicio")
        for h in highlights:
            try:
                hid = str(h.get("id"))
                chunk_text, payload = _build_highlight_chunk(h)
                if not chunk_text.strip():
                    continue
                sha1 = hashlib.sha1(chunk_text.encode("utf-8")).hexdigest()
                row = con.execute(
                    "SELECT sha1 FROM readwise_items WHERE id = ?", (hid,)
                ).fetchone()
                if row and row[0] == sha1:
                    stats["highlights_skipped"] += 1
                    continue
                if row:
                    _delete_old(hid, "highlight")
                await _index_pairs([(chunk_text, payload)])
                con.execute(
                    "INSERT OR REPLACE INTO readwise_items(id,kind,sha1,indexed_at,chunks) VALUES (?,?,?,?,?)",
                    (hid, "highlight", sha1, now_iso, 1),
                )
                con.commit()
                stats["highlights_new"] += 1
            except Exception as e:
                logger.exception("error indexando highlight %s", h.get("id"))
                stats["errors"] += 1
        _set_cursor(con, "highlight", now_iso)
    except Exception as e:
        logger.exception("highlights sync failed: %s", e)
        stats["errors"] += 1

    # ── Documents ──
    doc_cursor = _get_cursor(con, "document")
    try:
        docs = _fetch_documents(doc_cursor)
        logger.info("Reader documents fetched: %d (delta desde %s)",
                    len(docs), doc_cursor or "inicio")
        for d in docs:
            try:
                did = str(d.get("id"))
                pairs = _build_document_chunks(d)
                if not pairs:
                    continue
                # SHA1 del content completo (independiente del chunking)
                content = d.get("content", "") or d.get("summary", "")
                sha1 = hashlib.sha1((content or "").encode("utf-8")).hexdigest()
                row = con.execute(
                    "SELECT sha1 FROM readwise_items WHERE id = ?", (did,)
                ).fetchone()
                if row and row[0] == sha1:
                    stats["docs_skipped"] += 1
                    continue
                if row:
                    _delete_old(did, "document")
                await _index_pairs(pairs)
                con.execute(
                    "INSERT OR REPLACE INTO readwise_items(id,kind,sha1,indexed_at,chunks) VALUES (?,?,?,?,?)",
                    (did, "document", sha1, now_iso, len(pairs)),
                )
                con.commit()
                stats["docs_new"] += 1
            except Exception as e:
                logger.exception("error indexando doc %s", d.get("id"))
                stats["errors"] += 1
        _set_cursor(con, "document", now_iso)
    except Exception as e:
        logger.exception("documents sync failed: %s", e)
        stats["errors"] += 1

    logger.info("=== readwise import end · %s ===", stats)
    return stats


if __name__ == "__main__":
    s = asyncio.run(run_import())
    sys.exit(0 if s["errors"] == 0 else 1)
