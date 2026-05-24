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
from qdrant_service import search, tenant_collection, resolve_kb_collection, resolve_mem_collection
from config import QDRANT_COLLECTION, QDRANT_MEMORY_COLLECTION
from auth import get_optional_user
from claude_service import chat_with_rag, stream_chat_with_rag, run_agent, stream_agent, analyze_document
from openai_brain import (
    chat_openai, stream_chat_openai,
    run_agent_openai, stream_agent_openai,
    GPT_MINI, GPT_4O,
)
from gemini_brain import chat_gemini, stream_chat_gemini, GEMINI_FLASH_LITE
from groq_brain import run_agent_groq, stream_agent_groq, chat_groq, stream_chat_groq, LLAMA_70B
from codex_brain import run_codex, stream_codex
from ollama_brain import chat_ollama, stream_chat_ollama, OLLAMA_CHAT_MODEL
from memory_service import save_turn, save_learning, get_semantic_context, get_business_context, get_learnings_context
from openai_service import extract_learning, classify_complexity, needs_tools
from topic_memory_service import detect_topics, get_topic_memories, update_topics_background
import cost_service
from insforge import get_tenant, increment_usage, orchestrate_tenant

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
    "ligero":   "Gemini 2.5 Flash-Lite",
    "simple":   f"GPT-4o-mini",
    "rapido":   "Llama 3.3 70B (Groq)",
    "medio":    f"GPT-4o",
    "complejo": "Claude Sonnet 4.6",
    "agente":   f"GPT-4o + tools",
    "codex":    "Codex CLI + filesystem",
    "local":    f"Ollama {OLLAMA_CHAT_MODEL} (GPU local)",
}


class ChatRequest(BaseModel):
    pregunta: str
    categoria: Optional[str] = None
    top_k: int = 5
    session_id: str = "default"
    usar_memoria: bool = True
    modo: Optional[str] = None  # "ligero"|"simple"|"rapido"|"medio"|"complejo"|"agente"|"codex"|"local" | None (auto)
    # "rapido" → Llama 3.3 70B via Groq. 3x más rápido que GPT-4o, ~76% más barato.
    # Sin caching, calidad ligeramente inferior a GPT-4o. Solo opt-in explícito
    # (el auto-clasificador NO lo elige).
    # Backend para agente: "openai" (gpt-4o, default) | "groq" (llama-3.3-70b)
    # Sólo aplica cuando modo == "agente". Groq es 76% más barato y 3x más
    # rápido pero sin caching y tool use menos maduro.
    agente_provider: Optional[str] = "openai"
    # Sólo aplica cuando modo == "codex". Directorio donde Codex CLI debe operar.
    # Si None, codex_brain usa os.getcwd() del proceso mollo-brain (suele NO ser
    # lo que quieres) — el caller (ej. MCP) debería pasarlo explícito.
    workdir: Optional[str] = None


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
        score  = round(r.score, 3) if hasattr(r, "score") else 1.0
        text   = r.payload.get("text", "")
        snippets.append(f"[{source} | {cat} | relevancia: {score}]\n{text}")
    return "\n\n---\n\n".join(snippets)


def _extract_referenced_filenames(prompt: str) -> list[str]:
    """Extrae filenames que el usuario menciona explícitamente en su prompt.
    Mira primero el patrón que inyecta el frontend tras un upload:
        [Documentos disponibles vía RAG: "X.docx", "Y.pdf"]
    Si no encuentra ese marcador, busca extensiones comunes en texto libre.
    """
    import re
    files: list[str] = []
    m = re.search(r'Documentos disponibles vía RAG:\s*([^\]]+)', prompt)
    if m:
        files.extend(re.findall(r'"([^"]+)"', m.group(1)))
    if not files:
        files.extend(re.findall(
            r'[\w\-\.]+\.(?:docx?|pdf|txt|md|csv|xlsx?|json|html?|xml)',
            prompt,
            re.IGNORECASE,
        ))
    # dedupe preservando orden
    seen, uniq = set(), []
    for f in files:
        if f not in seen:
            seen.add(f); uniq.append(f)
    return uniq


def _fetch_chunks_by_filename(filenames: list[str], collection: str,
                              max_chunks_per_file: int = 8) -> list:
    """Trae chunks de Qdrant filtrando por source==filename. Útil cuando el
    usuario referencia un archivo específico que la búsqueda semántica no
    rankea alto (típico cuando el filename no aparece en el contenido)."""
    if not filenames:
        return []
    from qdrant_service import client
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
    out = []
    for fn in filenames:
        try:
            pts = client.scroll(
                collection_name=collection,
                scroll_filter=Filter(must=[
                    FieldCondition(key="source", match=MatchValue(value=fn)),
                ]),
                limit=max_chunks_per_file,
                with_payload=True,
            )[0]
            # Mantener orden por chunk index para coherencia
            pts.sort(key=lambda p: p.payload.get("chunk", 0))
            out.extend(pts)
        except Exception:
            pass
    return out


def _save_in_background(
    background_tasks: BackgroundTasks,
    pregunta: str, respuesta: str,
    session_id: str, query_vector: list,
    modo: str = "medio",
    usage: dict | None = None,
    tenant_slug: str | None = None,
    mem_coll: str | None = None,
):
    def _work():
        try:
            save_turn(pregunta, respuesta, session_id, vector=query_vector,
                      mem_collection=mem_coll or QDRANT_MEMORY_COLLECTION)
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


def _collect_context(query_vector: list, req: "ChatRequest",  # noqa: F821
                     mem_coll: str = QDRANT_MEMORY_COLLECTION):
    memory_context = get_semantic_context(query_vector, mem_collection=mem_coll) if req.usar_memoria else ""
    # El contexto estático (business/learnings/topic) es del OWNER. Solo se inyecta
    # en el camino legacy; a usuarios aislados no se les filtra.
    if req.usar_memoria and mem_coll == QDRANT_MEMORY_COLLECTION:
        business_ctx, learnings_ctx, topic_memory = _get_static_context()
    else:
        business_ctx = learnings_ctx = topic_memory = ""
    return memory_context, business_ctx, learnings_ctx, topic_memory


async def _respond(modo: str, pregunta: str, doc_context: str,
                   memory_context: str, business_ctx: str,
                   learnings_ctx: str, topic_memory: str,
                   system_prompt: str | None = None,
                   agente_provider: str = "openai",
                   workdir: str | None = None) -> tuple[str, dict]:
    if modo == "codex":
        return await run_codex(pregunta, workdir=workdir)
    kwargs = dict(
        pregunta=pregunta,
        doc_context=doc_context,
        memory_context=memory_context,
        business_context=business_ctx,
        learnings_context=learnings_ctx,
        topic_memory=topic_memory,
        system_prompt=system_prompt,
    )
    if modo == "agente":
        if agente_provider == "groq":
            return await run_agent_groq(**kwargs, model=LLAMA_70B)
        return await run_agent_openai(**kwargs, model=GPT_4O)
    if modo == "ligero":
        return chat_gemini(**kwargs, model=GEMINI_FLASH_LITE)
    if modo == "local":
        return chat_ollama(**kwargs, model=OLLAMA_CHAT_MODEL)
    if modo == "simple":
        return chat_openai(**kwargs, model=GPT_MINI)
    if modo == "rapido":
        return chat_groq(**kwargs, model=LLAMA_70B)
    if modo == "medio":
        return chat_openai(**kwargs, model=GPT_4O)
    return chat_with_rag(**kwargs)


async def _stream(modo: str, pregunta: str, doc_context: str,
                  memory_context: str, business_ctx: str,
                  learnings_ctx: str, topic_memory: str,
                  system_prompt: str | None = None,
                  agente_provider: str = "openai",
                  workdir: str | None = None):
    if modo == "codex":
        async for chunk in stream_codex(pregunta, workdir=workdir):
            yield chunk
        return
    kwargs = dict(
        pregunta=pregunta,
        doc_context=doc_context,
        memory_context=memory_context,
        business_context=business_ctx,
        learnings_context=learnings_ctx,
        topic_memory=topic_memory,
        system_prompt=system_prompt,
    )
    # Smart routing: solo cargamos agent loop (tools schema + system prompt
    # extendido) cuando la query da señales de necesitar tools. Para chit-chat
    # y queries conceptuales, chat path ahorra ~1100 tokens y ~3-5s latencia.
    # `agente` (explícito) y `ligero` no pasan por este check.
    tools_needed = needs_tools(pregunta) if modo not in ("agente", "ligero") else False

    if modo == "agente":
        if agente_provider == "groq":
            async for chunk in stream_agent_groq(**kwargs, model=LLAMA_70B):
                yield chunk
        else:
            async for chunk in stream_agent_openai(**kwargs, model=GPT_4O):
                yield chunk
    elif modo == "ligero":
        async for chunk in stream_chat_gemini(**kwargs, model=GEMINI_FLASH_LITE):
            yield chunk
    elif modo == "local":
        async for chunk in stream_chat_ollama(**kwargs, model=OLLAMA_CHAT_MODEL):
            yield chunk
    elif modo == "simple":
        if tools_needed:
            async for chunk in stream_agent_openai(**kwargs, model=GPT_MINI):
                yield chunk
        else:
            async for chunk in stream_chat_openai(**kwargs, model=GPT_MINI):
                yield chunk
    elif modo == "rapido":
        if tools_needed:
            async for chunk in stream_agent_groq(**kwargs, model=LLAMA_70B):
                yield chunk
        else:
            async for chunk in stream_chat_groq(**kwargs, model=LLAMA_70B):
                yield chunk
    elif modo == "medio":
        if tools_needed:
            async for chunk in stream_agent_openai(**kwargs, model=GPT_4O):
                yield chunk
        else:
            async for chunk in stream_chat_openai(**kwargs, model=GPT_4O):
                yield chunk
    else:  # complejo → Claude (agent si hay intent de tools, chat si no)
        if tools_needed:
            claude_kwargs = {k: v for k, v in kwargs.items() if k != "system_prompt"}
            async for chunk in stream_agent(**claude_kwargs):
                yield chunk
        else:
            async for chunk in stream_chat_with_rag(**kwargs):
                yield chunk


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/ask")
async def ask_mollo(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    tenant: dict | None = Depends(get_tenant),
    user: dict | None = Depends(get_optional_user),
):
    if not req.pregunta.strip():
        raise HTTPException(400, "La pregunta no puede estar vacía")

    coll         = resolve_kb_collection(tenant, user)
    mem_coll     = resolve_mem_collection(user)
    referenced   = _extract_referenced_filenames(req.pregunta)

    # modo:local + usar_memoria=False → modelo local sin RAG. El modelo chico se
    # distrae con doc_context inyectado; al pedir explícitamente sin memoria,
    # omitimos embedding, búsqueda y contexto. (_collect_context ya devuelve ""
    # en memoria/business/learnings cuando usar_memoria=False.)
    if req.modo == "local" and not req.usar_memoria and tenant is None:
        query_vector = None
        results      = []
        doc_context  = ""
    else:
        query_vector = await get_embedding(req.pregunta)
        results      = search(query_vector, top_k=req.top_k, categoria=req.categoria, collection=coll)
        # Si el usuario referenció un archivo específico (típico tras upload desde
        # el chat), trae sus chunks aunque la búsqueda semántica no los haya
        # rankeado alto — el filename no siempre aparece dentro del contenido.
        forced_pts   = _fetch_chunks_by_filename(referenced, collection=coll) if referenced else []
        doc_context  = _build_doc_context(forced_pts + list(results))

    # Tenant externo: InsForge recopila respuesta completa sin contexto de Mollo
    if tenant:
        from insforge import _classify, _MODELO_LABEL
        import anthropic as _ac, openai as _oc

        modo   = req.modo or _classify(req.pregunta)
        system = tenant.get("system_prompt") or (
            "Eres un asistente especializado en estrategia y negocios. "
            "Responde en el idioma del usuario. Usa markdown."
        )
        user_content = req.pregunta
        if doc_context:
            user_content = f"DOCUMENTOS RELEVANTES:\n{doc_context}\n\nPREGUNTA: {req.pregunta}"

        if modo == "complejo":
            import os as _os
            cl = _ac.Anthropic(api_key=_os.getenv("ANTHROPIC_API_KEY"))
            msg = cl.messages.create(
                model=_os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            respuesta = msg.content[0].text
            usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens, "model": _os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")}
        else:
            import os as _os
            model = "gpt-4o" if modo == "medio" else "gpt-4o-mini"
            oc = _oc.OpenAI(api_key=_os.getenv("OPENAI_API_KEY"))
            resp = oc.chat.completions.create(
                model=model, max_tokens=2048,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            )
            respuesta = resp.choices[0].message.content
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens, "model": model}

        background_tasks.add_task(increment_usage, tenant["id"])
        return {
            "respuesta": respuesta,
            "modo": modo,
            "modelo": _MODELO_LABEL.get(modo, modo),
            "fuentes_consultadas": len(results),
            "fuentes": [{"archivo": r.payload.get("source"), "categoria": r.payload.get("categoria"), "relevancia": round(r.score, 3)} for r in results],
        }

    # Pipeline Mollo (usuario interno)
    memory_context, business_ctx, learnings_ctx, topic_memory = _collect_context(query_vector, req, mem_coll)
    modo = req.modo or classify_complexity(req.pregunta)

    # Slim para agente: tiene tools para fetchar info, no necesita RAG ni topic_memory
    # cargados a priori. Reduce input ~30-50% por iteración. Se preserva doc_context
    # SOLO si el usuario nombró un archivo específicamente — ahí sí lo necesita.
    if modo == "agente":
        if not referenced:
            doc_context = ""
        topic_memory = ""

    # Slim para ligero: queries triviales (hola, ok, cómo estás) — cero context.
    # Una respuesta corta no necesita historia, RAG, ni learnings. Reduce de
    # ~500 tok a ~30 tok input.
    if modo == "ligero":
        doc_context = memory_context = business_ctx = learnings_ctx = topic_memory = ""

    # Codex lee los archivos del proyecto directamente — todo el contexto de
    # Mollo (RAG, memoria, business, learnings) es ruido y desperdicia tokens.
    if modo == "codex":
        doc_context = memory_context = business_ctx = learnings_ctx = topic_memory = ""

    respuesta, usage = await _respond(
        modo, req.pregunta, doc_context,
        memory_context, business_ctx, learnings_ctx, topic_memory,
        agente_provider=req.agente_provider or "openai",
        workdir=req.workdir,
    )

    _save_in_background(background_tasks, req.pregunta, respuesta,
                        req.session_id, query_vector, modo=modo, usage=usage,
                        tenant_slug=None, mem_coll=mem_coll)

    # Etiqueta dinámica para agente Groq
    modelo_label = MODELO_LABEL.get(modo, modo)
    if modo == "agente" and (req.agente_provider or "openai") == "groq":
        modelo_label = "Llama 3.3 70B + tools"

    return {
        "respuesta":          respuesta,
        "modo":               modo,
        "modelo":             modelo_label,
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
    user: dict | None = Depends(get_optional_user),
):
    if not req.pregunta.strip():
        raise HTTPException(400, "La pregunta no puede estar vacía")

    coll         = resolve_kb_collection(tenant, user)
    mem_coll     = resolve_mem_collection(user)
    referenced   = _extract_referenced_filenames(req.pregunta)

    # modo:local + usar_memoria=False → modelo local sin RAG. El modelo chico se
    # distrae con doc_context inyectado; al pedir explícitamente sin memoria,
    # omitimos embedding, búsqueda y contexto. (_collect_context ya devuelve ""
    # en memoria/business/learnings cuando usar_memoria=False.)
    if req.modo == "local" and not req.usar_memoria and tenant is None:
        query_vector = None
        results      = []
        doc_context  = ""
    else:
        query_vector = await get_embedding(req.pregunta)
        results      = search(query_vector, top_k=req.top_k, categoria=req.categoria, collection=coll)
        # Si el usuario referenció un archivo específico (típico tras upload desde
        # el chat), trae sus chunks aunque la búsqueda semántica no los haya
        # rankeado alto — el filename no siempre aparece dentro del contenido.
        forced_pts   = _fetch_chunks_by_filename(referenced, collection=coll) if referenced else []
        doc_context  = _build_doc_context(forced_pts + list(results))

    # ── Tenant externo: InsForge orquesta directamente (sin contexto de Mollo) ──
    if tenant:
        collected: list[str] = []
        stream_usage: dict = {}
        # Captura el modo desde el header \x02{modo}:{label}\n que emite
        # orchestrate_tenant — antes lo guardábamos como `modo=<modelo>` por bug.
        captured_modo: dict = {"value": None}

        async def generate_tenant():
            async for chunk in orchestrate_tenant(
                tenant, req.pregunta, doc_context,
                modo_override=req.modo or None,
            ):
                if chunk.startswith("\x03"):
                    try:
                        stream_usage.update(_json.loads(chunk[1:]))
                    except Exception:
                        pass
                    continue
                # Header de modo va al cliente (UI lo lee) Y lo capturamos
                # para registrarlo correctamente en cost_log
                if chunk.startswith("\x02") and captured_modo["value"] is None:
                    nl = chunk.find("\n")
                    meta = chunk[1:nl] if nl != -1 else chunk[1:]
                    # formato "modo:label" — sólo nos interesa el modo
                    captured_modo["value"] = meta.split(":", 1)[0] or None
                collected.append(chunk)
                yield chunk

            # Emite resumen al cliente: modelo usado, tokens, requests restantes
            req_remaining = max(0, tenant["req_limit"] - tenant["req_used"] - 1)
            summary = {
                "model":         stream_usage.get("model", ""),
                "input_tokens":  stream_usage.get("input_tokens", 0),
                "output_tokens": stream_usage.get("output_tokens", 0),
                "req_remaining": req_remaining,
                "req_limit":     tenant["req_limit"],
            }
            yield f"\x04{_json.dumps(summary)}"

            background_tasks.add_task(increment_usage, tenant["id"])
            if collected:
                import cost_service as _cs
                from topic_memory_service import detect_topics as _dt
                def _log():
                    try:
                        topics = _dt(req.pregunta)
                        topic  = topics[0] if topics else "general"
                        _cs.record(
                            model=stream_usage.get("model", "gpt-4o-mini"),
                            modo=captured_modo["value"] or req.modo or "tenant",
                            input_tokens=stream_usage.get("input_tokens", 0),
                            output_tokens=stream_usage.get("output_tokens", 0),
                            cache_read_tokens=stream_usage.get("cache_read_tokens", 0),
                            query_preview=req.pregunta[:80],
                            topic=topic,
                            tenant_slug=tenant["slug"],
                        )
                    except Exception:
                        pass
                background_tasks.add_task(_log)

        return StreamingResponse(generate_tenant(), media_type="text/plain; charset=utf-8")

    # ── Pipeline Mollo (usuario interno — con contexto completo) ─────────────
    memory_context, business_ctx, learnings_ctx, topic_memory = _collect_context(query_vector, req, mem_coll)
    modo = req.modo or classify_complexity(req.pregunta)

    # Slim para agente: tiene tools para fetchar info — no necesita RAG ni topic_memory
    # cargados a priori. Reduce input ~30-50% por iteración. doc_context SOLO si
    # el usuario nombró un archivo específicamente.
    if modo == "agente":
        if not referenced:
            doc_context = ""
        topic_memory = ""

    # Slim para ligero: queries triviales — cero context inyectado.
    if modo == "ligero":
        doc_context = memory_context = business_ctx = learnings_ctx = topic_memory = ""

    # Codex lee el filesystem — el contexto inyectado de Mollo es ruido.
    if modo == "codex":
        doc_context = memory_context = business_ctx = learnings_ctx = topic_memory = ""

    mollo_collected: list[str] = []
    mollo_usage: dict = {}

    async def generate_mollo():
        modelo_label = MODELO_LABEL.get(modo, modo)
        if modo == "agente" and (req.agente_provider or "openai") == "groq":
            modelo_label = "Llama 3.3 70B + tools"
        yield f"\x02{modo}:{modelo_label}\n"
        async for chunk in _stream(
            modo, req.pregunta, doc_context,
            memory_context, business_ctx, learnings_ctx, topic_memory,
            agente_provider=req.agente_provider or "openai",
            workdir=req.workdir,
        ):
            if chunk.startswith("\x03"):
                try:
                    mollo_usage.update(_json.loads(chunk[1:]))
                except Exception:
                    pass
                continue
            mollo_collected.append(chunk)
            yield chunk

        _save_in_background(
            background_tasks, req.pregunta, "".join(mollo_collected),
            req.session_id, query_vector,
            modo=modo, usage=mollo_usage or None,
            tenant_slug=None, mem_coll=mem_coll,
        )

    return StreamingResponse(generate_mollo(), media_type="text/plain; charset=utf-8")


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
