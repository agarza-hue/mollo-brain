"""Memoria persistente de Mollo — guarda conversaciones y aprendizajes."""
import json, uuid
from datetime import datetime
from pathlib import Path
from config import MEMORY_FILE


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
):
    data = _load()
    entry = {
        "session_id": session_id,
        "fecha": datetime.now().isoformat(),
        "usuario": user_msg,
        "mollo": mollo_response[:500],
    }
    data["conversaciones"].append(entry)
    data["conversaciones"] = data["conversaciones"][-200:]
    _save(data)

    # Persistir en Qdrant para recuperación semántica futura
    if vector:
        try:
            from qdrant_service import upsert_memory_vector
            upsert_memory_vector(
                record_id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "usuario": user_msg,
                    "mollo": mollo_response[:500],
                    "fecha": entry["fecha"],
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
    _save(data)


def update_business_context(key: str, value):
    data = _load()
    data["contexto_negocio"][key] = {
        "valor": value,
        "actualizado": datetime.now().isoformat()
    }
    _save(data)


def get_recent_context(n: int = 10) -> str:
    """Devuelve las últimas N conversaciones como contexto para el prompt."""
    data = _load()
    recent = data["conversaciones"][-n:]
    if not recent:
        return ""
    lines = []
    for turn in recent:
        lines.append(f"Adolfo: {turn['usuario']}")
        lines.append(f"Mollo: {turn['mollo']}")
    return "\n".join(lines)


def get_business_context() -> str:
    data = _load()
    ctx = data.get("contexto_negocio", {})
    if not ctx:
        return ""
    lines = [f"- {k}: {v['valor']}" for k, v in ctx.items()]
    return "\n".join(lines)


def get_semantic_context(query_vector: list[float], top_k: int = 6) -> str:
    """Recupera conversaciones pasadas semánticamente relevantes para la pregunta actual."""
    try:
        from qdrant_service import search_memory
        results = search_memory(query_vector, top_k=top_k)
        if not results:
            return get_recent_context(4)  # fallback a recientes si no hay memoria vectorial
        lines = []
        for r in results:
            lines.append(f"Adolfo: {r.payload['usuario']}")
            lines.append(f"Mollo: {r.payload['mollo']}")
        return "\n".join(lines)
    except Exception:
        return get_recent_context(4)


def get_learnings_context(max_items: int = 20) -> str:
    """Devuelve aprendizajes deduplicados por tema, listos para inyectar al prompt."""
    data = _load()
    learnings = data.get("aprendizajes", [])
    if not learnings:
        return ""
    # Deduplicar por tema: si el mismo tema aparece varias veces, queda el más reciente
    seen: dict[str, str] = {}
    for entry in learnings:
        seen[entry["tema"]] = entry["insight"]
    items = list(seen.items())[-max_items:]
    return "\n".join(f"- [{tema}] {insight}" for tema, insight in items)


def get_all_memory() -> dict:
    return _load()
