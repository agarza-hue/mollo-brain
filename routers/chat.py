"""
Endpoints de chat con routing inteligente de modelos:
  simple   → GPT-4o-mini  (~4% del costo de Claude)
  medio    → GPT-4o        (~72% del costo de Claude)
  complejo → Claude Sonnet (calidad máxima)
  agente   → GPT-4o con tools (herramientas externas)
"""
import asyncio
import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

import json as _json

from embeddings import get_embedding
from qdrant_service import search, tenant_collection
from config import QDRANT_COLLECTION
from claude_service import chat_with_rag, stream_chat_with_rag, run_agent, stream_agent, analyze_document
from openai_brain import (
    chat_openai, stream_chat_openai,
    run_agent_openai, stream_agent_openai,
    GPT_MINI, GPT_4O,
)
from memory_service import save_turn, save_learning, get_semantic_context, get_business_context, get_learnings_context
from openai_service import extract_learning, classify_complexity
from topic_memory_service import detect_topics, get_topic_memories, update_topics_background
import cost_service
from insforge import get_tenant, increment_usage

# Cache de contexto estático — se invalida cada 5 minutos.
# Garantiza que el bloque cacheado de Anthropic llegue idéntico request tras request.
_STATIC_CTX: dict = {}
_STATIC_TTL = 300  # segundos


def _get_static_context() -> tuple[str, str, str]:
    """Devuelve (business_ctx, learnings_ctx, topic_memory) desde caché o recalcula."""
    now = time.monotonic()
    if now - _STATIC_CTX.get("ts", 0) > _STATIC_TTL:
        _STATIC_CTX.update({
            "business":   get_business_context(),
            "learnings":  get_learnings_context(),
            "topics":     get_topic_memories(["financiero", "estrategia", "ventas",
                                               "rrhh", "operaciones", "general"]),
            "ts":         now,
        })
    return _STATIC_CTX["business"], _STATIC_CTX["learnings"], _STATIC_CTX["topics"]


def _invalidate_static_cache():
    """Fuerza refresco en el próximo request (llamar tras guardar nueva memoria)."""
    _STATIC_CTX["ts"] = 0

router = APIRouter(prefix="/chat", tags=["Chat"])

# Mapa de nivel → modelo usado (para logging)
MODELO_LABEL = {
    "simple":   f"GPT-4o-mini",
    "medio":    f"GPT-4o",
    "complejo": "Claude Sonnet 4.6",
    "agente":   f"GPT-4o + tools",
}


class ChatRequest(BaseModel):
    pregunta: str
    categoria: Optional[str] = None
    top_k: int = 5
    session_id: str = "default"
    usar_memoria: bool = True
    modo: Optional[str] = None  # "simple"|"medio"|"complejo"|"agente" | None (auto)


class AnalyzeRequest(BaseModel):
    texto: str
    instruccion: Optional[str] = ""


def _build_doc_context(results: list) -> str:
    if not results:
        return ""
    snippets = []
    for r in results:
        source = r.payload.get("source", "desconocido")
        cat    = r.payload.get("categoria", "")
        score  = round(r.score, 3)
        text   = r.payload.get("text", "")
        snippets.append(f"[{source} | {cat} | relevancia: {score}]\n{text}")
    return "\n\n---\n\n".join(snippets)


def _save_in_background(
    background_tasks: BackgroundTasks,
    pregunta: str, respuesta: str,
    session_id: str, query_vector: list,
    modo: str = "medio",
    usage: dict | None = None,
    tenant_slug: str | None = None,
):
    def _work():
        try:
            save_turn(pregunta, respuesta, session_id, vector=query_vector)
            tema, insight = extract_learning(pregunta, respuesta)
            if insight:
                save_learning(tema, insight)
                _invalidate_static_cache()
            update_topics_background(pregunta, respuesta)
            if usage:
                from topic_memory_service import detect_topics as _dt
                topics = _dt(pregunta)
                topic  = topics[0] if topics else "general"
                cost_service.record(
                    model=usage.get("model", "gpt-4o-mini"),
                    modo=modo,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read_tokens=usage.get("cache_read_tokens", 0),
                    query_preview=pregunta,
                    topic=topic,
                    tenant_slug=tenant_slug,
                )
        except Exception:
            pass
    background_tasks.add_task(_work)


def _collect_context(query_vector: list, req: "ChatRequest"):  # noqa: F821
    memory_context = get_semantic_context(query_vector) if req.usar_memoria else ""
    if req.usar_memoria:
        business_ctx, learnings_ctx, topic_memory = _get_static_context()
    else:
        business_ctx = learnings_ctx = topic_memory = ""
    return memory_context, business_ctx, learnings_ctx, topic_memory


async def _respond(modo: str, pregunta: str, doc_context: str,
                   memory_context: str, business_ctx: str,
                   learnings_ctx: str, topic_memory: str) -> tuple[str, dict]:
    kwargs = dict(
        pregunta=pregunta,
        doc_context=doc_context,
        memory_context=memory_context,
        business_context=business_ctx,
        learnings_context=learnings_ctx,
        topic_memory=topic_memory,
    )
    if modo == "agente":
        return await run_agent_openai(**kwargs, model=GPT_4O)
    if modo == "simple":
        return chat_openai(**kwargs, model=GPT_MINI)
    if modo == "medio":
        return chat_openai(**kwargs, model=GPT_4O)
    return chat_with_rag(**kwargs)


async def _stream(modo: str, pregunta: str, doc_context: str,
                  memory_context: str, business_ctx: str,
                  learnings_ctx: str, topic_memory: str):
    kwargs = dict(
        pregunta=pregunta,
        doc_context=doc_context,
        memory_context=memory_context,
        business_context=business_ctx,
        learnings_context=learnings_ctx,
        topic_memory=topic_memory,
    )
    if modo == "agente":
        async for chunk in stream_agent_openai(**kwargs, model=GPT_4O):
            yield chunk
    elif modo == "simple":
        async for chunk in stream_chat_openai(**kwargs, model=GPT_MINI):
            yield chunk
    elif modo == "medio":
        async for chunk in stream_chat_openai(**kwargs, model=GPT_4O):
            yield chunk
    else:  # complejo → Claude
        async for chunk in stream_chat_with_rag(**kwargs):
            yield chunk


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/ask")
async def ask_mollo(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    tenant: dict | None = Depends(get_tenant),
):
    if not req.pregunta.strip():
        raise HTTPException(400, "La pregunta no puede estar vacía")

    query_vector = await get_embedding(req.pregunta)
    coll         = tenant_collection(tenant["slug"]) if tenant else QDRANT_COLLECTION
    results      = search(query_vector, top_k=req.top_k, categoria=req.categoria, collection=coll)
    doc_context  = _build_doc_context(results)

    memory_context, business_ctx, learnings_ctx, topic_memory = _collect_context(query_vector, req)

    modo = req.modo or classify_complexity(req.pregunta)

    respuesta, usage = await _respond(
        modo, req.pregunta, doc_context,
        memory_context, business_ctx, learnings_ctx, topic_memory,
    )

    if tenant:
        background_tasks.add_task(increment_usage, tenant["id"])

    _save_in_background(background_tasks, req.pregunta, respuesta,
                        req.session_id, query_vector, modo=modo, usage=usage,
                        tenant_slug=tenant["slug"] if tenant else None)

    return {
        "respuesta":          respuesta,
        "modo":               modo,
        "modelo":             MODELO_LABEL.get(modo, modo),
        "fuentes_consultadas": len(results),
        "fuentes": [
            {
                "archivo":    r.payload.get("source"),
                "categoria":  r.payload.get("categoria"),
                "relevancia": round(r.score, 3),
            }
            for r in results
        ],
    }


@router.post("/stream")
async def stream_mollo(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    tenant: dict | None = Depends(get_tenant),
):
    if not req.pregunta.strip():
        raise HTTPException(400, "La pregunta no puede estar vacía")

    query_vector = await get_embedding(req.pregunta)
    coll         = tenant_collection(tenant["slug"]) if tenant else QDRANT_COLLECTION
    results      = search(query_vector, top_k=req.top_k, categoria=req.categoria, collection=coll)
    doc_context  = _build_doc_context(results)

    memory_context, business_ctx, learnings_ctx, topic_memory = _collect_context(query_vector, req)

    modo = req.modo or classify_complexity(req.pregunta)

    collected: list[str] = []
    stream_usage: dict = {}

    async def generate():
        yield f"\x02{modo}:{MODELO_LABEL.get(modo, modo)}\n"
        async for chunk in _stream(
            modo, req.pregunta, doc_context,
            memory_context, business_ctx, learnings_ctx, topic_memory,
        ):
            if chunk.startswith("\x03"):
                # Usage sentinel — parse and store, don't send to client
                try:
                    stream_usage.update(_json.loads(chunk[1:]))
                except Exception:
                    pass
                continue
            collected.append(chunk)
            yield chunk

        full_response = "".join(collected)
        if tenant:
            background_tasks.add_task(increment_usage, tenant["id"])
        _save_in_background(
            background_tasks, req.pregunta, full_response,
            req.session_id, query_vector,
            modo=modo, usage=stream_usage or None,
            tenant_slug=tenant["slug"] if tenant else None,
        )

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    if not req.texto.strip():
        raise HTTPException(400, "El texto no puede estar vacío")
    resultado, usage = analyze_document(req.texto, req.instruccion)
    try:
        cost_service.record(
            model=usage.get("model", "claude-sonnet-4-6"),
            modo="analyze",
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            query_preview=req.instruccion or req.texto[:80],
            topic="general",
        )
    except Exception:
        pass
    return {"analisis": resultado}
