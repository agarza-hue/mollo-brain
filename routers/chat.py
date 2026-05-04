"""Endpoint principal de chat con RAG."""
import asyncio
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from embeddings import get_embedding
from qdrant_service import search
from claude_service import chat_with_rag, stream_chat_with_rag, analyze_document, extract_learning
from memory_service import save_turn, save_learning, get_semantic_context, get_business_context, get_learnings_context

router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatRequest(BaseModel):
    pregunta: str
    categoria: Optional[str] = None  # filtrar búsqueda por categoría
    top_k: int = 5
    session_id: str = "default"
    usar_memoria: bool = True


class AnalyzeRequest(BaseModel):
    texto: str
    instruccion: Optional[str] = ""


@router.post("/ask")
async def ask_mollo(req: ChatRequest, background_tasks: BackgroundTasks):
    if not req.pregunta.strip():
        raise HTTPException(400, "La pregunta no puede estar vacía")

    # 1. Embedding de la pregunta
    query_vector = await get_embedding(req.pregunta)

    # 2. Buscar documentos relevantes en Qdrant
    results = search(query_vector, top_k=req.top_k, categoria=req.categoria)

    doc_context = ""
    if results:
        snippets = []
        for r in results:
            source = r.payload.get("source", "desconocido")
            cat = r.payload.get("categoria", "")
            score = round(r.score, 3)
            text = r.payload.get("text", "")
            snippets.append(f"[{source} | {cat} | relevancia: {score}]\n{text}")
        doc_context = "\n\n---\n\n".join(snippets)

    # 3. Obtener contexto de memoria semántica + aprendizajes acumulados
    memory_context = get_semantic_context(query_vector) if req.usar_memoria else ""
    business_ctx = get_business_context() if req.usar_memoria else ""
    learnings_ctx = get_learnings_context() if req.usar_memoria else ""

    # 4. Generar respuesta con Claude (incluye aprendizajes en el contexto)
    respuesta = chat_with_rag(
        pregunta=req.pregunta,
        doc_context=doc_context,
        memory_context=memory_context,
        business_context=business_ctx,
        learnings_context=learnings_ctx,
    )

    # 5. Guardar turno en memoria (con vector para recuperación semántica futura)
    save_turn(req.pregunta, respuesta, req.session_id, vector=query_vector)

    # 6. Extraer y guardar aprendizaje en background — no bloquea la respuesta
    def _extract_and_save():
        try:
            tema, insight = extract_learning(req.pregunta, respuesta)
            if insight:
                save_learning(tema, insight)
        except Exception:
            pass

    background_tasks.add_task(_extract_and_save)

    return {
        "respuesta": respuesta,
        "fuentes_consultadas": len(results),
        "fuentes": [
            {
                "archivo": r.payload.get("source"),
                "categoria": r.payload.get("categoria"),
                "relevancia": round(r.score, 3),
            }
            for r in results
        ],
    }


@router.post("/stream")
async def stream_mollo(req: ChatRequest, background_tasks: BackgroundTasks):
    if not req.pregunta.strip():
        raise HTTPException(400, "La pregunta no puede estar vacía")

    query_vector = await get_embedding(req.pregunta)
    results = search(query_vector, top_k=req.top_k, categoria=req.categoria)

    doc_context = ""
    if results:
        snippets = []
        for r in results:
            source = r.payload.get("source", "desconocido")
            cat    = r.payload.get("categoria", "")
            score  = round(r.score, 3)
            text   = r.payload.get("text", "")
            snippets.append(f"[{source} | {cat} | relevancia: {score}]\n{text}")
        doc_context = "\n\n---\n\n".join(snippets)

    memory_context = get_semantic_context(query_vector) if req.usar_memoria else ""
    business_ctx   = get_business_context()              if req.usar_memoria else ""
    learnings_ctx  = get_learnings_context()             if req.usar_memoria else ""

    collected: list[str] = []

    async def generate():
        async for chunk in stream_chat_with_rag(
            pregunta=req.pregunta,
            doc_context=doc_context,
            memory_context=memory_context,
            business_context=business_ctx,
            learnings_context=learnings_ctx,
        ):
            collected.append(chunk)
            yield chunk

        full_response = "".join(collected)
        save_turn(req.pregunta, full_response, req.session_id, vector=query_vector)

        def _learn():
            try:
                tema, insight = extract_learning(req.pregunta, full_response)
                if insight:
                    save_learning(tema, insight)
            except Exception:
                pass

        background_tasks.add_task(_learn)

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Analiza un texto libre sin RAG."""
    if not req.texto.strip():
        raise HTTPException(400, "El texto no puede estar vacío")
    resultado = analyze_document(req.texto, req.instruccion)
    return {"analisis": resultado}
