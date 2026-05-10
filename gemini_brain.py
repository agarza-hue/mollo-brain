"""
Gemini brain — modelo ultra-barato para tier `ligero`.

Casos de uso del tier `ligero`:
  - Saludos / acks ("hola", "ok gracias")
  - Preguntas triviales ("qué hora es", "cómo estás")
  - Confirmaciones cortas que no requieren razonamiento

Pricing gemini-2.5-flash-lite:
  - Input:    $0.10/1M tokens
  - Output:   $0.40/1M tokens
  - Cached:   $0.01/1M tokens (90% off)

Vs gpt-4o-mini ($0.15/$0.60): ~33% más barato input, ~33% más barato output.
"""
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import google.generativeai as genai
from config import GEMINI_API_KEY, GEMINI_FLASH_LITE_MODEL, GEMINI_PRO_MODEL

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

GEMINI_FLASH_LITE = GEMINI_FLASH_LITE_MODEL
GEMINI_PRO        = GEMINI_PRO_MODEL


# Prompt corto para tier ligero — Mollo conversacional sin contexto pesado.
# El tier ligero NO necesita la enciclopedia de identidad del MOLLO_SYSTEM
# completo; eso desperdicia tokens en queries de 5 palabras.
LIGERO_SYSTEM = """Eres Mollo, asistente personal de Adolfo Garza. Responde en español de forma directa, breve y natural. Sin disclaimers ni preámbulos. Máximo 2-3 frases."""


def _build_prompt(pregunta: str, system_prompt: str | None = None) -> str:
    """Gemini API combina system + user en un solo prompt."""
    sys = system_prompt or LIGERO_SYSTEM
    return f"{sys}\n\nUsuario: {pregunta}"


def chat_gemini(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GEMINI_FLASH_LITE,
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    """Una sola llamada Gemini. Tier ligero ignora la mayoría del context.

    Sólo se inyecta `memory_context` (recientes) si está, para coherencia
    conversacional ("antes me dijiste X" / "como te pregunté ayer").
    """
    parts = []
    if memory_context:
        parts.append(f"Conversación reciente:\n{memory_context}")
    parts.append(f"Pregunta: {pregunta}")
    user_msg = "\n\n".join(parts)
    full_prompt = _build_prompt(user_msg, system_prompt)

    m = genai.GenerativeModel(
        model,
        generation_config={
            "max_output_tokens": 256,   # ligero: respuestas cortas siempre
            "temperature":       0.6,
        },
    )
    r = m.generate_content(full_prompt)
    text = r.text or ""
    usage_meta = getattr(r, "usage_metadata", None)
    in_tok  = getattr(usage_meta, "prompt_token_count", 0) or 0
    out_tok = getattr(usage_meta, "candidates_token_count", 0) or 0
    cached  = getattr(usage_meta, "cached_content_token_count", 0) or 0

    usage = {
        "model": model,
        "input_tokens":  max(0, in_tok - cached),
        "output_tokens": out_tok,
        "cache_read_tokens": cached,
    }
    return text, usage


async def stream_chat_gemini(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GEMINI_FLASH_LITE,
    system_prompt: str | None = None,
):
    """Stream Gemini Flash-Lite — tier ligero. Mismos parámetros que stream_chat_openai."""
    import json as _json

    parts = []
    if memory_context:
        parts.append(f"Conversación reciente:\n{memory_context}")
    parts.append(f"Pregunta: {pregunta}")
    user_msg = "\n\n".join(parts)
    full_prompt = _build_prompt(user_msg, system_prompt)

    m = genai.GenerativeModel(
        model,
        generation_config={
            "max_output_tokens": 256,
            "temperature":       0.6,
        },
    )
    response = m.generate_content(full_prompt, stream=True)
    final_usage = None
    for chunk in response:
        if hasattr(chunk, "text") and chunk.text:
            yield chunk.text
        # El último chunk del stream trae usage_metadata acumulado
        if getattr(chunk, "usage_metadata", None):
            final_usage = chunk.usage_metadata

    in_tok  = getattr(final_usage, "prompt_token_count", 0) or 0
    out_tok = getattr(final_usage, "candidates_token_count", 0) or 0
    cached  = getattr(final_usage, "cached_content_token_count", 0) or 0
    usage = {
        "model": model,
        "input_tokens":  max(0, in_tok - cached),
        "output_tokens": out_tok,
        "cache_read_tokens": cached,
    }
    yield f"\x03{_json.dumps(usage)}"


# ─────────────────────────────────────────────────────────────────────────────
# Gemini 2.5 Pro — fallback de Claude Sonnet para queries complejas.
#
# Pricing (≤200K tok input):
#   Input:  $1.25/1M
#   Output: $10.00/1M
#   Cache:  $0.125/1M
#
# Trade-off vs Claude Sonnet 4.6 ($3.00/$15.00):
#   + 58% más barato input, 33% más barato output
#   + Acceso a 1M context window
#   − Caching menos generoso (90% off vs Anthropic 95% off)
#   − Razonamiento sutilmente distinto — usar como fallback, no como default
# ─────────────────────────────────────────────────────────────────────────────


def _build_full_prompt(
    pregunta: str,
    doc_context: str,
    memory_context: str,
    business_context: str,
    learnings_context: str,
    topic_memory: str,
    system_prompt: str | None,
) -> tuple[str, str]:
    """Construye (system, user) para Gemini Pro replicando el shape de Claude."""
    # Importamos lazy para no acoplar Gemini brain con Claude brain
    from claude_service import MOLLO_SYSTEM as CLAUDE_MOLLO_SYSTEM
    sys = system_prompt or CLAUDE_MOLLO_SYSTEM

    parts = []
    if business_context:
        parts.append(f"CONTEXTO DEL NEGOCIO:\n{business_context}")
    if learnings_context:
        parts.append(f"APRENDIZAJES:\n{learnings_context}")
    if topic_memory:
        parts.append(f"MEMORIA POR TEMAS:\n{topic_memory}")
    if memory_context:
        parts.append(f"CONVERSACIONES RECIENTES:\n{memory_context}")
    if doc_context:
        parts.append(f"DOCUMENTOS RELEVANTES:\n{doc_context}")
    parts.append(f"PREGUNTA: {pregunta}")
    user_msg = "\n\n".join(parts)
    return sys, user_msg


def chat_gemini_pro(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GEMINI_PRO,
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    sys, user_msg = _build_full_prompt(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory, system_prompt,
    )
    full_prompt = f"{sys}\n\n{user_msg}"
    m = genai.GenerativeModel(
        model,
        generation_config={
            "max_output_tokens": 4096,
            "temperature":       0.7,
        },
    )
    r = m.generate_content(full_prompt)
    text = r.text or ""
    meta = getattr(r, "usage_metadata", None)
    in_tok  = getattr(meta, "prompt_token_count", 0) or 0
    out_tok = getattr(meta, "candidates_token_count", 0) or 0
    cached  = getattr(meta, "cached_content_token_count", 0) or 0
    usage = {
        "model": model,
        "input_tokens":  max(0, in_tok - cached),
        "output_tokens": out_tok,
        "cache_read_tokens": cached,
    }
    return text, usage


async def stream_chat_gemini_pro(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GEMINI_PRO,
    system_prompt: str | None = None,
):
    import json as _json
    sys, user_msg = _build_full_prompt(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory, system_prompt,
    )
    full_prompt = f"{sys}\n\n{user_msg}"
    m = genai.GenerativeModel(
        model,
        generation_config={
            "max_output_tokens": 4096,
            "temperature":       0.7,
        },
    )
    response = m.generate_content(full_prompt, stream=True)
    final_meta = None
    for chunk in response:
        if hasattr(chunk, "text") and chunk.text:
            yield chunk.text
        if getattr(chunk, "usage_metadata", None):
            final_meta = chunk.usage_metadata

    in_tok  = getattr(final_meta, "prompt_token_count", 0) or 0
    out_tok = getattr(final_meta, "candidates_token_count", 0) or 0
    cached  = getattr(final_meta, "cached_content_token_count", 0) or 0
    usage = {
        "model": model,
        "input_tokens":  max(0, in_tok - cached),
        "output_tokens": out_tok,
        "cache_read_tokens": cached,
    }
    yield f"\x03{_json.dumps(usage)}"
