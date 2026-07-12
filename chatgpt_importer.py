"""
Importador del historial de ChatGPT → Qdrant.

Uso:
  python chatgpt_importer.py /ruta/a/conversations.json
  python chatgpt_importer.py /ruta/a/chatgpt_export.zip

El export se obtiene en ChatGPT → Ajustes → Controles de datos → Exportar datos.
"""
import json
import zipfile
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# ── Formato del conversations.json de ChatGPT ─────────────────────────────────
#
# Lista de conversaciones. Cada una tiene:
#   title, create_time, mapping (árbol de nodos)
#   Cada nodo: {id, message: {author: {role}, content: {parts: [...]}}, parent, children}
#
# ─────────────────────────────────────────────────────────────────────────────

CHATGPT_COLLECTION = "chatgpt_historial"
CHUNK_SIZE          = 4          # pares pregunta-respuesta por chunk
MIN_CONTENT_LEN     = 30         # ignorar mensajes muy cortos


def _extract_text(message: dict) -> str:
    """Extrae texto plano de un mensaje de ChatGPT (soporta text y code blocks)."""
    if not message:
        return ""
    content = message.get("content", {})
    if not content:
        return ""
    parts = content.get("parts", [])
    texts = []
    for part in parts:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict):
            # bloques de código, imágenes, etc.
            text = part.get("text") or part.get("content") or ""
            if text:
                texts.append(text)
    return " ".join(texts).strip()


def _walk_conversation(mapping: dict) -> list[dict]:
    """Recorre el árbol de mensajes y devuelve la lista ordenada por create_time."""
    messages = []
    for node in mapping.values():
        msg = node.get("message")
        if not msg:
            continue
        role = msg.get("author", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(msg)
        if len(text) < MIN_CONTENT_LEN:
            continue
        messages.append({
            "role":  role,
            "text":  text,
            "ts":    msg.get("create_time") or 0,
        })
    messages.sort(key=lambda m: m["ts"])
    return messages


def _build_chunks(messages: list[dict], title: str, fecha: str) -> list[str]:
    """
    Agrupa mensajes en chunks de CHUNK_SIZE pares (user + assistant).
    Cada chunk lleva el título de la conversación como contexto.
    """
    # Emparejar user/assistant consecutivos
    pairs = []
    i = 0
    while i < len(messages) - 1:
        if messages[i]["role"] == "user" and messages[i + 1]["role"] == "assistant":
            pairs.append((messages[i]["text"], messages[i + 1]["text"]))
            i += 2
        else:
            i += 1

    if not pairs:
        return []

    chunks = []
    for start in range(0, len(pairs), CHUNK_SIZE):
        group = pairs[start: start + CHUNK_SIZE]
        lines = [f"Conversación: {title} ({fecha})"]
        for user_msg, ai_msg in group:
            lines.append(f"Usuario: {user_msg[:500]}")
            lines.append(f"ChatGPT: {ai_msg[:800]}")
        chunks.append("\n".join(lines))
    return chunks


def parse_conversations(path: str) -> list[dict]:
    """
    Lee conversations.json (o ZIP que lo contiene) y devuelve
    lista de {title, fecha, chunks: [str]}.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No encontré el archivo: {path}")

    # Soportar ZIP o JSON directo
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
            json_name = next((n for n in names if "conversations" in n and n.endswith(".json")), None)
            if not json_name:
                raise ValueError(f"No encontré conversations.json dentro del ZIP. Archivos: {names}")
            with zf.open(json_name) as f:
                data = json.load(f)
    else:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

    results = []
    for conv in data:
        title   = conv.get("title") or "Sin título"
        ts      = conv.get("create_time") or conv.get("update_time") or 0
        fecha   = datetime.fromtimestamp(ts).strftime("%d/%m/%Y") if ts else "fecha desconocida"
        mapping = conv.get("mapping") or {}
        msgs    = _walk_conversation(mapping)
        chunks  = _build_chunks(msgs, title, fecha)
        if chunks:
            results.append({"title": title, "fecha": fecha, "ts": ts, "chunks": chunks})

    return results


# ── Indexado en Qdrant ────────────────────────────────────────────────────────

async def _ensure_chatgpt_collection():
    from qdrant_client import QdrantClient
    from qdrant_client.models import VectorParams, Distance
    from config import QDRANT_HOST, QDRANT_PORT, QDRANT_API_KEY

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY or None)
    cols = [c.name for c in client.get_collections().collections]
    if CHATGPT_COLLECTION not in cols:
        client.create_collection(
            collection_name=CHATGPT_COLLECTION,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )
    return client


async def _upsert_chunks(client, chunks_data: list[dict]):
    from qdrant_client.models import PointStruct
    from embeddings import get_embedding

    points = []
    for item in chunks_data:
        vector = await get_embedding(item["text"])
        points.append(PointStruct(
            id=str(uuid4()),
            vector=vector,
            payload={
                "text":    item["text"],
                "title":   item["title"],
                "fecha":   item["fecha"],
                "source":  "chatgpt",
            },
        ))

    # Insertar en lotes de 50
    for i in range(0, len(points), 50):
        client.upsert(collection_name=CHATGPT_COLLECTION, points=points[i:i + 50])

    return len(points)


async def import_chatgpt(path: str, verbose: bool = True) -> dict:
    """
    Función principal: parsea el export y lo indexa en Qdrant.
    Devuelve estadísticas del proceso.
    """
    if verbose:
        print(f"Leyendo: {path}")

    conversations = parse_conversations(path)
    total_convs   = len(conversations)
    total_chunks  = sum(len(c["chunks"]) for c in conversations)

    if verbose:
        print(f"Conversaciones encontradas: {total_convs}")
        print(f"Chunks a indexar: {total_chunks}")

    client = await _ensure_chatgpt_collection()

    # Aplanar todos los chunks con metadata
    all_chunks = []
    for conv in conversations:
        for chunk in conv["chunks"]:
            all_chunks.append({
                "text":  chunk,
                "title": conv["title"],
                "fecha": conv["fecha"],
            })

    indexed = await _upsert_chunks(client, all_chunks)

    if verbose:
        print(f"Indexados: {indexed} vectores en '{CHATGPT_COLLECTION}'")

    return {
        "conversaciones": total_convs,
        "chunks_indexados": indexed,
        "coleccion": CHATGPT_COLLECTION,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python chatgpt_importer.py <conversations.json o export.zip>")
        sys.exit(1)

    result = asyncio.run(import_chatgpt(sys.argv[1]))
    print(f"\nListo. {result['conversaciones']} conversaciones → {result['chunks_indexados']} vectores en Qdrant.")
