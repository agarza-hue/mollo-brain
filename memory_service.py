"""Memoria persistente de Mollo — conversaciones, aprendizajes, contexto negocio."""
import json, uuid
from datetime import datetime
from pathlib import Path
from config import MEMORY_FILE, QDRANT_MEMORY_COLLECTION


def _load() -> dict:
    if not Path(MEMORY_FILE).exists():
        return {"conversaciones": [], "aprendizajes": [], "contexto_negocio": {}}
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_turn(
    user_msg: str,
    mollo_response: str,
    session_id: str = "default",
    vector: list[float] | None = None,
    mem_collection: str = QDRANT_MEMORY_COLLECTION,
):
    from openai_service import summarize_response

    summary = summarize_response(mollo_response)
    fecha   = datetime.now().isoformat()

    # El JSON (MEMORY_FILE) es un store GLOBAL (fallback de últimas 20 convos).
    # Solo se escribe para el owner/legacy; los usuarios aislados viven solo en
    # su colección Qdrant per-usuario (sin tocar el JSON compartido).
    if mem_collection == QDRANT_MEMORY_COLLECTION:
        data = _load()
        data["conversaciones"].append({
            "session_id": session_id,
            "fecha": fecha,
            "usuario": user_msg,
            "mollo_summary": summary,
        })
        data["conversaciones"] = data["conversaciones"][-20:]
        _save(data)

    # Guardar respuesta completa en Qdrant para recuperación semántica
    if vector:
        try:
            from qdrant_service import upsert_memory_vector
            upsert_memory_vector(
                record_id=str(uuid.uuid4()),
                vector=vector,
                collection=mem_collection,
                payload={
                    "usuario": user_msg,
                    "mollo": mollo_response,
                    "mollo_summary": summary,
                    "fecha": fecha,
                    "session_id": session_id,
                },
            )
        except Exception:
            pass


def save_learning(topic: str, insight: str):
    data = _load()
    data["aprendizajes"].append({
        "fecha": datetime.now().isoformat(),
        "tema": topic,
        "insight": insight,
    })
    # Deduplicar por tema (última versión gana) y limitar a 100 entradas únicas.
    # Sin esto el archivo crece indefinidamente — cada conversación añade 1 entrada.
    seen: dict[str, dict] = {}
    for entry in data["aprendizajes"]:
        seen[entry["tema"]] = entry
    data["aprendizajes"] = list(seen.values())[-100:]
    _save(data)


def update_business_context(key: str, value):
    data = _load()
    data["contexto_negocio"][key] = {
        "valor": value,
        "actualizado": datetime.now().isoformat(),
    }
    _save(data)


def get_recent_context(n: int = 10) -> str:
    data = _load()
    recent = data["conversaciones"][-n:]
    if not recent:
        return ""
    lines = []
    for turn in recent:
        lines.append(f"Adolfo: {turn['usuario']}")
        lines.append(f"Mollo: {turn['mollo_summary']}")
    return "\n".join(lines)


def get_business_context() -> str:
    data = _load()
    ctx = data.get("contexto_negocio", {})
    if not ctx:
        return ""
    lines = [f"- {k}: {v['valor']}" for k, v in ctx.items()]
    return "\n".join(lines)


def get_semantic_context(query_vector: list[float], top_k: int = 5,
                         mem_collection: str = QDRANT_MEMORY_COLLECTION) -> str:
    # Legacy = owner/anónimo. Para usuarios aislados NO se mezcla ni el historial
    # global de ChatGPT ni el fallback del JSON global (ambos son del owner).
    is_legacy = mem_collection == QDRANT_MEMORY_COLLECTION
    try:
        from qdrant_service import search_memory, search_chatgpt
        parts = []

        # Conversaciones previas (colección per-usuario o legacy según mem_collection)
        mollo_results = search_memory(query_vector, top_k=top_k, collection=mem_collection)
        if mollo_results:
            lines = []
            for r in mollo_results:
                lines.append(f"Adolfo: {r.payload['usuario']}")
                summary = r.payload.get("mollo_summary") or r.payload.get("mollo", "")[:300]
                lines.append(f"Mollo: {summary}")
            parts.append("--- Conversaciones con Mollo ---\n" + "\n".join(lines))

        # Historial de ChatGPT (global del owner) — solo legacy
        if is_legacy:
            gpt_results = search_chatgpt(query_vector, top_k=3)
            if gpt_results:
                lines = []
                for r in gpt_results:
                    title = r.payload.get("title", "")
                    fecha = r.payload.get("fecha", "")
                    text  = r.payload.get("text", "")[:600]
                    lines.append(f"[ChatGPT — {title} ({fecha})]\n{text}")
                parts.append("--- Historial de ChatGPT ---\n" + "\n\n".join(lines))

        if parts:
            return "\n\n".join(parts)
        return get_recent_context(4) if is_legacy else ""
    except Exception:
        return get_recent_context(4) if is_legacy else ""


def get_learnings_context(max_items: int = 20) -> str:
    data = _load()
    learnings = data.get("aprendizajes", [])
    if not learnings:
        return ""
    seen: dict[str, str] = {}
    for entry in learnings:
        seen[entry["tema"]] = entry["insight"]
    items = list(seen.items())[-max_items:]
    return "\n".join(f"- [{tema}] {insight}" for tema, insight in items)


def get_all_memory() -> dict:
    return _load()
