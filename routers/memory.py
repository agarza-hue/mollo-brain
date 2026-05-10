"""Endpoints para gestión de memoria de Mollo — general y por temas."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from memory_service import get_all_memory, update_business_context, save_learning
from topic_memory_service import (
    get_all_topic_memories, get_topic_summary, clear_topic,
    manual_update_topic, TOPICS, detect_topics, get_topic_memories,
)

router = APIRouter(prefix="/memory", tags=["Memoria"])


class BusinessContextUpdate(BaseModel):
    clave: str
    valor: str


class LearningEntry(BaseModel):
    tema: str
    insight: str


class TopicUpdate(BaseModel):
    resumen: str
    hechos_clave: list[str] = []
    pendientes: list[str] = []


# ── Memoria general ───────────────────────────────────────────────────────────

@router.post("/claude-md/refresh")
def refresh_user_claude_md():
    """Fuerza re-fetch de CLAUDE.md desde Dropbox vault. Útil tras edición
    explícita del user (sin esperar el TTL de 5 min)."""
    from user_context_service import invalidate_cache, get_user_claude_md
    invalidate_cache()
    text = get_user_claude_md(force_refresh=True)
    return {
        "ok":     True,
        "chars":  len(text),
        "preview": text[:200] if text else "",
    }


@router.get("/claude-md")
def get_user_claude_md_endpoint():
    """Devuelve el contenido actual de CLAUDE.md (cached). Para debugging."""
    from user_context_service import get_user_claude_md
    text = get_user_claude_md()
    return {"chars": len(text), "text": text}


@router.get("/")
def get_memory():
    return get_all_memory()


@router.post("/business")
def set_business_context(req: BusinessContextUpdate):
    update_business_context(req.clave, req.valor)
    return {"status": "ok", "guardado": req.clave}


@router.post("/learning")
def add_learning(req: LearningEntry):
    save_learning(req.tema, req.insight)
    return {"status": "ok"}


# ── Memoria por temas ─────────────────────────────────────────────────────────

@router.get("/topics")
def get_topics():
    """Lista todos los temas con su estado de memoria."""
    data = get_all_topic_memories()
    result = {}
    for key, meta in TOPICS.items():
        topic_data = data.get(key, {})
        result[key] = {
            "nombre": meta["nombre"],
            "descripcion": meta["descripcion"],
            "tiene_memoria": bool(topic_data.get("resumen")),
            "actualizado": topic_data.get("actualizado"),
            "conversaciones_procesadas": topic_data.get("conversaciones_procesadas", 0),
            "resumen": topic_data.get("resumen", ""),
            "hechos_clave": topic_data.get("hechos_clave", []),
            "pendientes": topic_data.get("pendientes", []),
        }
    return result


@router.get("/topics/{topic_key}")
def get_topic(topic_key: str):
    if topic_key not in TOPICS:
        raise HTTPException(404, f"Tema '{topic_key}' no existe. Temas válidos: {list(TOPICS.keys())}")
    return {
        "key": topic_key,
        "meta": TOPICS[topic_key],
        "memoria": get_topic_summary(topic_key),
    }


@router.put("/topics/{topic_key}")
def update_topic(topic_key: str, req: TopicUpdate):
    if topic_key not in TOPICS:
        raise HTTPException(404, f"Tema '{topic_key}' no existe")
    manual_update_topic(topic_key, req.resumen, req.hechos_clave, req.pendientes)
    return {"status": "ok", "tema": topic_key}


@router.delete("/topics/{topic_key}")
def reset_topic(topic_key: str):
    if topic_key not in TOPICS:
        raise HTTPException(404, f"Tema '{topic_key}' no existe")
    clear_topic(topic_key)
    return {"status": "ok", "mensaje": f"Memoria de '{topic_key}' reseteada"}


@router.get("/topics/detect/{text}")
def detect_topics_endpoint(text: str):
    """Detecta qué temas toca un texto (útil para debugging)."""
    topics = detect_topics(text)
    return {"temas_detectados": topics, "nombres": [TOPICS[t]["nombre"] for t in topics]}
