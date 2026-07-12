"""
Ollama local — backend GPU sobre la RTX 5070 (modo `local`).

Cuándo usarlo:
  - Privacidad: datos que no deben salir de la red local.
  - Volumen/costo: tareas simples de alto volumen sin costo por token.
  - Baja dependencia de APIs externas (Claude/OpenAI) para lo trivial.

Trade-off vs Claude/GPT:
  + $0 por token, privado, corre en GPU local (Blackwell, CUDA 12).
  − Calidad inferior a Claude/GPT-4o en razonamiento y código complejos.

Usa el endpoint OpenAI-compatible de Ollama (`/v1`), así reutilizamos el mismo
armado de mensajes (`_build_messages`) y el sizing de tokens (`_max_tokens_for`)
que openai_brain. El cliente apunta al Ollama local; la api_key es dummy
(Ollama no la valida).
"""
import json as _json

from openai import OpenAI

from config import OLLAMA_HOST, OLLAMA_CHAT_MODEL
from openai_brain import _build_messages, _max_tokens_for

# Re-export para que el router lo importe junto a las funciones (patrón groq_brain).
__all__ = ["chat_ollama", "stream_chat_ollama", "OLLAMA_CHAT_MODEL"]

_ollama = OpenAI(base_url=OLLAMA_HOST.rstrip("/") + "/v1", api_key="ollama")


def _usage(u, model: str) -> dict:
    """Normaliza usage al formato que espera cost_service (cached=0: local, gratis)."""
    return {
        "input_tokens":      (u.prompt_tokens or 0) if u else 0,
        "output_tokens":     (u.completion_tokens or 0) if u else 0,
        "cache_read_tokens": 0,
        "model":             model,
    }


def chat_ollama(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = OLLAMA_CHAT_MODEL,
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    response = _ollama.chat.completions.create(
        model=model,
        max_tokens=_max_tokens_for(pregunta, model),
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory, system_prompt,
        ),
    )
    return response.choices[0].message.content, _usage(response.usage, model)


async def stream_chat_ollama(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = OLLAMA_CHAT_MODEL,
    system_prompt: str | None = None,
):
    stream = _ollama.chat.completions.create(
        model=model,
        max_tokens=_max_tokens_for(pregunta, model),
        stream=True,
        stream_options={"include_usage": True},
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory, system_prompt,
        ),
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta
        if chunk.usage:
            yield f"\x03{_json.dumps(_usage(chunk.usage, model))}"
