"""
Tareas auxiliares — GPT-4o-mini si hay créditos, Claude Haiku como fallback automático.
Todas las funciones son transparentes: el caller no sabe cuál modelo se usó.
"""
import json
from openai import OpenAI, RateLimitError, AuthenticationError
from anthropic import Anthropic
from config import OPENAI_API_KEY, OPENAI_MODEL_AUX, ANTHROPIC_API_KEY

_openai  = OpenAI(api_key=OPENAI_API_KEY)
_claude  = Anthropic(api_key=ANTHROPIC_API_KEY)
HAIKU    = "claude-haiku-4-5-20251001"

# Se pone en False si OpenAI falla por cuota — evita intentos repetidos en la sesión
_openai_available = True


def _chat_openai(messages: list[dict], max_tokens: int, json_mode: bool = False) -> str:
    kwargs = dict(
        model=OPENAI_MODEL_AUX,
        max_tokens=max_tokens,
        messages=messages,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    r = _openai.chat.completions.create(**kwargs)
    return r.choices[0].message.content


def _chat_haiku(prompt: str, max_tokens: int) -> str:
    r = _claude.messages.create(
        model=HAIKU,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.content[0].text


def _aux_call(prompt: str, max_tokens: int, json_mode: bool = False) -> str:
    """Intenta OpenAI; si falla por cuota usa Haiku."""
    global _openai_available
    if _openai_available:
        try:
            return _chat_openai(
                [{"role": "user", "content": prompt}], max_tokens, json_mode
            )
        except (RateLimitError, AuthenticationError):
            _openai_available = False
        except Exception:
            _openai_available = False
    return _chat_haiku(prompt, max_tokens)


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


def classify_complexity(question: str) -> str:
    """
    Clasifica la consulta en 3 niveles para routing de modelos:
      'agente'   → requiere herramientas externas (cualquier modelo con tools)
      'simple'   → factual, corto, conversión → GPT-4o-mini
      'medio'    → análisis moderado, resumen → GPT-4o
      'complejo' → estrategia profunda, multi-paso → Claude Sonnet
    """
    q_lower = question.lower()

    # Triggers de herramientas (tienen prioridad sobre todo)
    tool_triggers = [
        "busca", "búsca", "buscar", "internet", "web", "cotiza",
        "ejecuta", "reinicia", "reiniciar", "estado del vps", "vps",
        "envía", "enviar", "manda", "mandar", "workflow", "n8n",
        "logs", "docker", "ahora mismo", "en este momento",
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
