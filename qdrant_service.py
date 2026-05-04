"""Gestión de vectores en Qdrant."""
from typing import Optional
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue
)
from config import QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION, QDRANT_MEMORY_COLLECTION

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

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


# ── Memoria semántica ─────────────────────────────────────────────────────────

def upsert_memory_vector(record_id: str, vector: list[float], payload: dict):
    client.upsert(
        collection_name=QDRANT_MEMORY_COLLECTION,
        points=[PointStruct(id=record_id, vector=vector, payload=payload)],
    )


def search_memory(query_vector: list[float], top_k: int = 6) -> list:
    results = client.query_points(
        collection_name=QDRANT_MEMORY_COLLECTION,
        query=query_vector,
        limit=top_k,
        with_payload=True,
    )
    return results.points


def upsert_vectors(records: list[dict], embeddings: list[list[float]]):
    points = [
        PointStruct(id=rec["id"], vector=emb, payload=rec["payload"])
        for rec, emb in zip(records, embeddings)
    ]
    client.upsert(collection_name=QDRANT_COLLECTION, points=points)


def search(query_vector: list[float], top_k: int = 5, categoria: Optional[str] = None):
    search_filter = None
    if categoria:
        search_filter = Filter(
            must=[FieldCondition(key="categoria", match=MatchValue(value=categoria))]
        )
    results = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vector,
        limit=top_k,
        query_filter=search_filter,
        with_payload=True,
    )
    return results.points


def delete_by_source(filename: str):
    from qdrant_client.models import FilterSelector
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=filename))]
            )
        ),
    )


def collection_stats() -> dict:
    info = client.get_collection(QDRANT_COLLECTION)
    return {
        "total_vectores": info.points_count,
        "status": info.status,
    }
