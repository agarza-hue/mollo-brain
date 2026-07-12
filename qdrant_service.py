"""Gestión de vectores en Qdrant."""
from typing import Optional
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue
)
from config import (
    QDRANT_HOST, QDRANT_PORT, QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_MEMORY_COLLECTION,
    PER_USER_ISOLATION, OWNER_USER_ID,
)
from chatgpt_importer import CHATGPT_COLLECTION

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY or None,
                      https=False)

VECTOR_SIZE = 768  # nomic-embed-text


def _ensure(collection_name: str):
    cols = [c.name for c in client.get_collections().collections]
    if collection_name not in cols:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def ensure_collection():
    _ensure(QDRANT_COLLECTION)


def ensure_memory_collection():
    _ensure(QDRANT_MEMORY_COLLECTION)


def ensure_chatgpt_collection():
    _ensure(CHATGPT_COLLECTION)


# ── Memoria semántica ─────────────────────────────────────────────────────────

def upsert_memory_vector(record_id: str, vector: list[float], payload: dict,
                         collection: str = QDRANT_MEMORY_COLLECTION):
    _ensure(collection)
    client.upsert(
        collection_name=collection,
        points=[PointStruct(id=record_id, vector=vector, payload=payload)],
    )


def search_memory(query_vector: list[float], top_k: int = 6,
                  collection: str = QDRANT_MEMORY_COLLECTION) -> list:
    _ensure(collection)
    results = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=top_k,
        with_payload=True,
    )
    return results.points


def tenant_collection(slug: str) -> str:
    return f"sinergy_{slug}"


# ── Aislamiento por usuario (MolloIA) ─────────────────────────────────────────
def user_collection(user_id: str) -> str:
    return f"mollo_u_{user_id}"


def user_memory_collection(user_id: str) -> str:
    return f"mollo_mem_u_{user_id}"


def _is_isolated_user(user: Optional[dict]) -> bool:
    """True si el request es de un usuario MolloIA aislado (flag ON y no es el owner)."""
    return bool(
        PER_USER_ISOLATION and user and user.get("id")
        and str(user["id"]) != OWNER_USER_ID
    )


def resolve_kb_collection(tenant: Optional[dict], user: Optional[dict]) -> str:
    """Colección de conocimiento: tenant SinergyOS → sinergy_*; usuario MolloIA
    aislado → mollo_u_*; owner/anónimo (o flag OFF) → legacy global."""
    if tenant:
        return tenant_collection(tenant["slug"])
    if _is_isolated_user(user):
        return user_collection(user["id"])
    return QDRANT_COLLECTION


def resolve_mem_collection(user: Optional[dict]) -> str:
    if _is_isolated_user(user):
        return user_memory_collection(user["id"])
    return QDRANT_MEMORY_COLLECTION


def upsert_vectors(records: list[dict], embeddings: list[list[float]], collection: str = QDRANT_COLLECTION):
    _ensure(collection)
    points = [
        PointStruct(id=rec["id"], vector=emb, payload=rec["payload"])
        for rec, emb in zip(records, embeddings)
    ]
    client.upsert(collection_name=collection, points=points)


def search(query_vector: list[float], top_k: int = 5, categoria: Optional[str] = None, collection: str = QDRANT_COLLECTION):
    _ensure(collection)
    search_filter = None
    if categoria:
        search_filter = Filter(
            must=[FieldCondition(key="categoria", match=MatchValue(value=categoria))]
        )
    results = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=top_k,
        query_filter=search_filter,
        with_payload=True,
    )
    return results.points


def delete_by_source(filename: str, collection: str = QDRANT_COLLECTION):
    from qdrant_client.models import FilterSelector
    client.delete(
        collection_name=collection,
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=filename))]
            )
        ),
    )


def search_chatgpt(query_vector: list[float], top_k: int = 4) -> list:
    """Busca en el historial importado de ChatGPT."""
    cols = [c.name for c in client.get_collections().collections]
    if CHATGPT_COLLECTION not in cols:
        return []
    results = client.query_points(
        collection_name=CHATGPT_COLLECTION,
        query=query_vector,
        limit=top_k,
        with_payload=True,
    )
    return results.points


def collection_stats() -> dict:
    info     = client.get_collection(QDRANT_COLLECTION)
    mem_info = client.get_collection(QDRANT_MEMORY_COLLECTION)
    cols     = [c.name for c in client.get_collections().collections]
    chatgpt_count = 0
    if CHATGPT_COLLECTION in cols:
        chatgpt_count = client.get_collection(CHATGPT_COLLECTION).points_count
    return {
        "total_vectores":     info.points_count,
        "memoria_vectores":   mem_info.points_count,
        "chatgpt_vectores":   chatgpt_count,
        "status":             info.status,
    }
