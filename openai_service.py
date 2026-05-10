"""
Tareas auxiliares — Llama 3.1 8B (Groq) primary, GPT-4o-mini fallback, Haiku último.
Todas las funciones son transparentes: el caller no sabe cuál modelo se usó.
"""
import json, time
from openai import OpenAI, RateLimitError, AuthenticationError
from anthropic import Anthropic
from config import (
    OPENAI_API_KEY, OPENAI_MODEL_AUX, ANTHROPIC_API_KEY,
    GROQ_API_KEY, LLAMA8B_MODEL,
)

_openai  = OpenAI(api_key=OPENAI_API_KEY)
_claude  = Anthropic(api_key=ANTHROPIC_API_KEY)
HAIKU    = "claude-haiku-4-5-20251001"

# Groq SDK opcional: si no hay key o falla import, se salta y usa OpenAI
try:
    from groq import Groq
    _groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except Exception:
    _groq = None

# Disable temporal por proveedor — evita reintentos en cascada en la sesión.
# Cool-down de 5 min permite recuperación si la falla fue transitoria.
_provider_state = {"groq": (True, 0), "openai": (True, 0)}
_COOLDOWN = 300  # segundos antes de reintentar un provider que falló


def _is_available(provider: str) -> bool:
    ok, ts = _provider_state[provider]
    if ok:
        return True
    if time.monotonic() - ts > _COOLDOWN:
        _provider_state[provider] = (True, 0)
        return True
    return False


def _disable(provider: str):
    _provider_state[provider] = (False, time.monotonic())


def _chat_groq(messages: list[dict], max_tokens: int, json_mode: bool = False) -> tuple[str, dict]:
    kwargs = dict(
        model=LLAMA8B_MODEL,
        max_tokens=max_tokens,
        messages=messages,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    r = _groq.chat.completions.create(**kwargs)
    usage = {
        "model": LLAMA8B_MODEL,
        "input_tokens":  r.usage.prompt_tokens or 0,
        "output_tokens": r.usage.completion_tokens or 0,
        "cache_read_tokens": 0,
    }
    return r.choices[0].message.content, usage


def _chat_openai(messages: list[dict], max_tokens: int, json_mode: bool = False) -> tuple[str, dict]:
    kwargs = dict(
        model=OPENAI_MODEL_AUX,
        max_tokens=max_tokens,
        messages=messages,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    r = _openai.chat.completions.create(**kwargs)
    cached = getattr(getattr(r.usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    full   = r.usage.prompt_tokens or 0
    usage = {
        "model": OPENAI_MODEL_AUX,
        "input_tokens":  max(0, full - cached),
        "output_tokens": r.usage.completion_tokens or 0,
        "cache_read_tokens": cached,
    }
    return r.choices[0].message.content, usage


def _chat_haiku(prompt: str, max_tokens: int) -> tuple[str, dict]:
    r = _claude.messages.create(
        model=HAIKU,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = {
        "model": HAIKU,
        "input_tokens":  r.usage.input_tokens or 0,
        "output_tokens": r.usage.output_tokens or 0,
        "cache_read_tokens": 0,
    }
    return r.content[0].text, usage


def _record_aux(usage: dict, query_preview: str = ""):
    """Registra el call auxiliar en cost_log para que el dashboard lo vea."""
    try:
        import cost_service
        cost_service.record(
            model=usage.get("model", ""),
            modo="aux",
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            query_preview=query_preview[:80],
            topic="aux",
        )
    except Exception:
        pass


def _aux_call(prompt: str, max_tokens: int, json_mode: bool = False, _preview: str = "") -> str:
    """Cadena de proveedores: Groq Llama 8B → OpenAI mini → Anthropic Haiku.
    Cada uno se desactiva 5 min ante fallo y se reintenta luego."""
    messages = [{"role": "user", "content": prompt}]

    # Tier 1: Groq Llama 3.1 8B (más barato, más rápido)
    if _groq is not None and _is_available("groq"):
        try:
            content, usage = _chat_groq(messages, max_tokens, json_mode)
            _record_aux(usage, _preview or prompt[:80])
            return content
        except Exception:
            _disable("groq")

    # Tier 2: OpenAI mini
    if _is_available("openai"):
        try:
            content, usage = _chat_openai(messages, max_tokens, json_mode)
            _record_aux(usage, _preview or prompt[:80])
            return content
        except (RateLimitError, AuthenticationError):
            _disable("openai")
        except Exception:
            _disable("openai")

    # Tier 3: Haiku (último resorte, no se desactiva)
    content, usage = _chat_haiku(prompt, max_tokens)
    _record_aux(usage, _preview or prompt[:80])
    return content


# ── API pública ───────────────────────────────────────────────────────────────

def extract_learning(question: str, answer: str) -> tuple[str, str]:
    prompt = f"""De esta conversación extrae en JSON:
{{"tema": "tema en 3-5 palabras", "insight": "aprendizaje clave en 1 oración"}}

Pregunta: {question}
Respuesta: {answer[:400]}

Solo JSON, sin texto adicional."""
    try:
        raw = _aux_call(prompt, max_tokens=120, json_mode=True)
        # Haiku puede devolver markdown con ```json
        raw = raw.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(raw)
        return data.get("tema", "general"), data.get("insight", "")
    except Exception:
        return "general", ""


def summarize_response(response: str) -> str:
    """Resumen de 2 oraciones para almacenar en memoria JSON."""
    if len(response) <= 350:
        return response
    prompt = f"Resume en máximo 2 oraciones los puntos más importantes:\n\n{response[:2000]}"
    try:
        return _aux_call(prompt, max_tokens=150).strip()
    except Exception:
        return response[:350]


_LIGERO_GREETINGS = {
    "hola", "holi", "buenas", "buenos días", "buenas tardes", "buenas noches",
    "qué tal", "que tal", "hi", "hey", "hello", "saludos",
}
_LIGERO_ACKS = {
    "ok", "okay", "okey", "vale", "perfecto", "entendido", "gracias",
    "sí", "si", "no", "claro", "listo", "anotado", "👍", "👌", "ya",
}
# Sólo trivia social — hora/fecha/clima requieren tools reales y van a `agente`.
# Si entran aquí, Gemini Flash-Lite alucina datos.
_LIGERO_TRIVIA = (
    "cómo estás", "como estas", "cómo te va", "como te va",
    "qué onda", "que onda", "cómo va", "como va", "todo bien",
)


def _is_ligero(question: str) -> bool:
    """Detecta queries triviales que no necesitan razonamiento ni context.
    Conservador: prefiere no clasificar como ligero si hay duda."""
    q = question.strip().lower().rstrip("?¿!¡.,")
    if not q:
        return False
    words = q.split()
    # Saludos/acks como mensaje completo (≤3 palabras)
    if len(words) <= 3:
        if q in _LIGERO_GREETINGS or q in _LIGERO_ACKS:
            return True
        # Combinaciones tipo "ok gracias", "hola mollo"
        if all(w in _LIGERO_GREETINGS | _LIGERO_ACKS | {"mollo", "tú", "tu"} for w in words):
            return True
    # Preguntas triviales fijas
    if any(t in q for t in _LIGERO_TRIVIA) and len(words) <= 6:
        return True
    return False


def classify_complexity(question: str) -> str:
    """
    Clasifica la consulta en niveles para routing de modelos:
      'ligero'   → trivial (saludo/ack/hora) → Gemini 2.5 Flash-Lite
      'agente'   → requiere herramientas externas (cualquier modelo con tools)
      'simple'   → factual, corto, conversión → GPT-4o-mini
      'medio'    → análisis moderado, resumen → GPT-4o
      'complejo' → estrategia profunda, multi-paso → Claude Sonnet
    """
    # Tier ligero ANTES que tool_triggers — un "ok gracias" no debe disparar agente
    if _is_ligero(question):
        return "ligero"

    q_lower = question.lower()

    # Triggers de herramientas (tienen prioridad sobre todo)
    tool_triggers = [
        "busca", "búsca", "buscar", "internet", "web", "cotiza",
        "ejecuta", "reinicia", "reiniciar", "estado del vps", "vps",
        "envía", "enviar", "manda", "mandar", "workflow", "n8n",
        "logs", "docker", "ahora mismo", "en este momento",
        "qué hora", "que hora", "qué día es", "que dia es",
        "qué fecha", "que fecha", "fecha de hoy",
        "convierte", "convertir", "dólar", "dolares", "dólares",
        "peso", "pesos", "mxn", "usd", "tipo de cambio",
        "dropbox", "archivo", "descarga", "sube", "subir", "carpeta",
        "pdf", "excel", "word", "analiza el archivo", "lee el archivo",
    ]
    if any(t in q_lower for t in tool_triggers):
        return "agente"

    # Triggers de alta complejidad → Claude
    complex_triggers = [
        "estrategia", "analiza", "compara", "propón", "diseña", "plan",
        "iso 9001", "auditoría", "reestructura", "modelo de negocio",
        "ventaja competitiva", "okr", "roadmap", "due diligence",
        "por qué", "qué haría", "cómo mejorar", "qué opinas",
    ]
    if any(t in q_lower for t in complex_triggers):
        return "complejo"

    # Preguntas cortas/simples → GPT-4o-mini
    if len(question.split()) <= 12:
        return "simple"

    # Default intermedio → GPT-4o
    return "medio"


def aux_json_call(prompt: str, max_tokens: int = 600) -> dict:
    """Llamada auxiliar que devuelve JSON. Usada por topic_memory_service."""
    try:
        raw = _aux_call(prompt, max_tokens=max_tokens, json_mode=True)
        raw = raw.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {}
