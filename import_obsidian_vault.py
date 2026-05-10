"""
Importa el vault de Obsidian (Dropbox /Obsidian/vault/) a Qdrant — RAG layer.

Polea cada N min vía systemd timer. Idempotente: usa hash SHA1 del contenido
como dedup key. Solo reindexea archivos nuevos o modificados.

Excluye:
  - CLAUDE.md y README.md de la raíz (son contexto/docs, no RAG content)
  - Archivos cuyo nombre empieza con `_` (placeholders, drafts privados)
  - Cualquier path bajo `.obsidian/` (workspace state, plugins)

Indexa con `categoria='vault'` en collection `mollo_empresa`.

Uso:
    /root/venv/bin/python -m import_obsidian_vault
"""
import os
import sys
import hashlib
import logging
import sqlite3
import asyncio
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv("/root/mollo_brain/.env")

from dropbox_service import get_client
from document_service import _chunk_text
from qdrant_service import upsert_vectors, client as qclient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue
from embeddings import get_embedding
from config import QDRANT_COLLECTION
import dropbox
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("vault_import")

VAULT_ROOT     = "/Obsidian/vault"
EXCLUDE_FILES  = {"CLAUDE.md", "README.md"}
EXCLUDE_PREFIX = "_"
DEDUP_DB       = os.path.expanduser("~/.mollo/vault_dedup.sqlite")


def _ensure_dedup_db():
    """Tabla con (path, sha1) — registra qué versión de cada archivo ya indexamos."""
    os.makedirs(os.path.dirname(DEDUP_DB), exist_ok=True)
    con = sqlite3.connect(DEDUP_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS vault_files (
            path TEXT PRIMARY KEY,
            sha1 TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            chunks INTEGER NOT NULL
        )
    """)
    con.commit()
    return con


def _walk_vault(dbx) -> list[dict]:
    """Lista recursiva de archivos .md en /Obsidian/vault/. Filtra excluidos."""
    files = []
    res = dbx.files_list_folder(VAULT_ROOT, recursive=True)
    while True:
        for entry in res.entries:
            if not isinstance(entry, dropbox.files.FileMetadata):
                continue
            name = entry.name
            path = entry.path_display
            if not name.endswith(".md"):
                continue
            if name in EXCLUDE_FILES and path.count("/") == 3:
                # CLAUDE.md/README.md solo en la raíz del vault
                continue
            if name.startswith(EXCLUDE_PREFIX):
                continue
            if "/.obsidian/" in path.lower():
                continue
            files.append({
                "path":     path,
                "name":     name,
                "size":     entry.size,
                "modified": entry.client_modified.isoformat() if entry.client_modified else "",
            })
        if not res.has_more:
            break
        res = dbx.files_list_folder_continue(res.cursor)
    return files


def _download_text(dbx, path: str) -> str:
    """Descarga un .md de Dropbox y devuelve su contenido como str."""
    _, response = dbx.files_download(path)
    return response.content.decode("utf-8", errors="replace")


def _delete_old_chunks_for_path(source_path: str):
    """Borra chunks viejos de Qdrant para este source antes de re-indexar."""
    try:
        qclient.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=Filter(must=[
                FieldCondition(key="source", match=MatchValue(value=source_path)),
                FieldCondition(key="categoria", match=MatchValue(value="vault")),
            ]),
        )
    except Exception as e:
        logger.warning("no se pudieron borrar chunks viejos de %s: %s", source_path, e)


async def _index_file(dbx, file_meta: dict, dedup_con) -> tuple[bool, int]:
    """Indexa un archivo. Returns (indexed, chunks_count)."""
    path = file_meta["path"]
    text = _download_text(dbx, path)
    sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()

    # Skip si ya indexamos esta versión exacta
    cur = dedup_con.execute("SELECT sha1 FROM vault_files WHERE path = ?", (path,))
    row = cur.fetchone()
    if row and row[0] == sha1:
        return False, 0

    if not text.strip():
        logger.info("skip empty %s", path)
        return False, 0

    # Si había una versión vieja, borrar sus chunks
    if row:
        _delete_old_chunks_for_path(path)

    # Chunkear, embedder, upsert
    chunks = _chunk_text(text)
    if not chunks:
        return False, 0

    records = []
    for i, chunk in enumerate(chunks):
        records.append({
            "id": str(uuid.uuid4()),
            "text": chunk,
            "payload": {
                "source":    path,            # ruta Dropbox como source
                "categoria": "vault",
                "chunk":     i,
                "total_chunks": len(chunks),
                "text":      chunk,
                "vault_modified": file_meta["modified"],
            },
        })
    embeddings = [await get_embedding(r["text"]) for r in records]
    upsert_vectors(records, embeddings, collection=QDRANT_COLLECTION)

    dedup_con.execute(
        "INSERT OR REPLACE INTO vault_files(path, sha1, indexed_at, chunks) VALUES (?, ?, ?, ?)",
        (path, sha1, datetime.now().isoformat(), len(chunks)),
    )
    dedup_con.commit()
    logger.info("indexed %s — %d chunks", path, len(chunks))
    return True, len(chunks)


async def run_import() -> dict:
    """Entry point. Returns stats."""
    logger.info("=== vault import start ===")
    dbx = get_client()
    dedup_con = _ensure_dedup_db()

    files = _walk_vault(dbx)
    logger.info("vault tiene %d archivos .md (excluyendo CLAUDE/README/_*)", len(files))

    total_chunks = 0
    indexed = 0
    skipped = 0

    for f in files:
        try:
            ok, n = await _index_file(dbx, f, dedup_con)
            if ok:
                indexed += 1
                total_chunks += n
            else:
                skipped += 1
        except Exception as e:
            logger.exception("error indexando %s: %s", f["path"], e)

    stats = {
        "total_files": len(files),
        "indexed":     indexed,
        "skipped":     skipped,
        "total_chunks": total_chunks,
    }
    logger.info("=== vault import end · %s ===", stats)
    return stats


if __name__ == "__main__":
    stats = asyncio.run(run_import())
    sys.exit(0 if stats["indexed"] >= 0 else 1)
