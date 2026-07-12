"""R3 — Caché semántica de respuestas para el Brain.

Reusa Qdrant (768d, COSINE) y el query_vector (nomic) que el pipeline de chat YA
calcula → cero embeddings extra. Devuelve una respuesta previa VERBATIM cuando la
consulta es casi idéntica (coseno >= HIT_THRESHOLD), por tenant y dentro de TTL.

Inerte salvo ANSWER_CACHE_ENABLED=1. Falla-cerrado: cualquier error o duda →
sin hit, nunca rompe el chat. Colección separada: no toca memoria ni docs.
"""
import os
import time
import uuid

from qdrant_client.models import (
    VectorParams, Distance, PointStruct, Filter, FieldCondition, MatchValue,
)
from qdrant_service import client

COLLECTION    = os.getenv("ANSWER_CACHE_COLLECTION", "mollo_answer_cache")
HIT_THRESHOLD = float(os.getenv("ANSWER_CACHE_HIT", "0.97"))
TTL_DAYS      = float(os.getenv("ANSWER_CACHE_TTL_DAYS", "7"))
SKIP_MODOS    = {"agente", "codex"}        # tools / efectos / no-determinismo
VECTOR_SIZE   = 768                        # nomic-embed-text


def _enabled() -> bool:
    # Se lee en cada llamada para poder togglear sin reiniciar el módulo.
    return os.getenv("ANSWER_CACHE_ENABLED", "0") == "1"


def ensure() -> None:
    names = {c.name for c in client.get_collections().collections}
    if COLLECTION not in names:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def lookup(query_vector, modo, tenant_slug=None):
    """Devuelve (answer|None, score). None salvo coseno alto, misma tenant y dentro de TTL."""
    if not _enabled() or query_vector is None or modo in SKIP_MODOS:
        return None, 0.0
    try:
        ensure()
        flt = Filter(must=[FieldCondition(
            key="tenant", match=MatchValue(value=tenant_slug or "_owner"))])
        res = client.query_points(
            collection_name=COLLECTION, query=query_vector,
            query_filter=flt, limit=1, score_threshold=HIT_THRESHOLD,
            with_payload=True).points
    except Exception:
        return None, 0.0                   # falla-cerrado
    if not res:
        return None, 0.0
    top = res[0]
    payload = top.payload or {}
    if TTL_DAYS and (time.time() - payload.get("ts", 0)) > TTL_DAYS * 86400:
        return None, 0.0
    return payload.get("answer"), float(top.score)


def store(query_vector, pregunta, answer, modo, tenant_slug=None) -> None:
    if not _enabled() or query_vector is None or modo in SKIP_MODOS:
        return
    a = (answer or "").strip()
    if len(a) < 40 or len(a) > 32768:      # ni errores/vacíos ni respuestas enormes
        return
    try:
        ensure()
        client.upsert(collection_name=COLLECTION, points=[PointStruct(
            id=str(uuid.uuid4()), vector=query_vector,
            payload={"pregunta": pregunta[:500], "answer": a, "modo": modo,
                     "tenant": tenant_slug or "_owner", "ts": time.time()})])
    except Exception:
        pass                               # la caché nunca rompe el flujo principal
