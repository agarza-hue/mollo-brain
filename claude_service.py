"""Claude como cerebro principal de Mollo — RAG + análisis + memoria + prompt caching."""
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

client       = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
async_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

MOLLO_SYSTEM = """Eres Mollo, el asistente empresarial ejecutivo de Adolfo. Nombrado así por su perro.

IDENTIDAD:
- Ejecutivo de alto nivel: directo, práctico, sin rodeos
- Hablas en español, sin errores ortográficos ni frases incompletas
- Pensamiento estratégico y analítico
- Proactivo: anticipas necesidades, identificas riesgos y oportunidades

EXPERTISE: Finanzas · Estrategia · RRHH · Ventas · PMO · ISO 9001 · VPS · IA aplicada

FORMATO DE RESPUESTA:
1. Respuesta directa (lo que Adolfo necesita saber YA)
2. Análisis estratégico (por qué y contexto)
3. Acción recomendada (pasos concretos)
4. Consideraciones (riesgos, alternativas)

REGLAS DE ORO:
- NUNCA dejes frases incompletas
- SIEMPRE termina con conclusión cerrada
- Si hay documentos de contexto, úsalos como base factual
- Cita la fuente del documento cuando usas información específica
- Si falta información crítica, pregunta específicamente
"""

# System prompt como bloque con cache_control — se cachea en la primera llamada
# y se reutiliza en las siguientes hasta 5 min de inactividad.
_SYSTEM_CACHED = [
    {
        "type": "text",
        "text": MOLLO_SYSTEM,
        "cache_control": {"type": "ephemeral"},
    }
]


def _build_messages(
    pregunta: str,
    doc_context: str,
    memory_context: str,
    business_context: str,
    learnings_context: str,
) -> list[dict]:
    """
    Construye el array de mensajes con cache_control estratégico:
      - Bloque 1 (cached):  contexto de negocio + aprendizajes  → casi nunca cambia
      - Bloque 2 (dynamic): memoria semántica + docs + pregunta → cambia cada turno
    """
    content = []

    # ── Bloque estático cacheado ──────────────────────────────────────────────
    static_parts = []
    if business_context:
        static_parts.append(f"CONTEXTO DEL NEGOCIO DE ADOLFO:\n{business_context}")
    if learnings_context:
        static_parts.append(f"APRENDIZAJES PREVIOS DE MOLLO:\n{learnings_context}")

    if static_parts:
        content.append({
            "type": "text",
            "text": "\n\n".join(static_parts),
            "cache_control": {"type": "ephemeral"},
        })

    # ── Bloque dinámico (sin caché) ───────────────────────────────────────────
    dynamic_parts = []
    if memory_context:
        dynamic_parts.append(f"CONVERSACIONES RECIENTES:\n{memory_context}")
    if doc_context:
        dynamic_parts.append(f"DOCUMENTOS RELEVANTES ENCONTRADOS:\n{doc_context}")
    dynamic_parts.append(f"PREGUNTA DE ADOLFO: {pregunta}")

    content.append({
        "type": "text",
        "text": "\n\n".join(dynamic_parts),
    })

    return [{"role": "user", "content": content}]


# ── Respuesta completa ────────────────────────────────────────────────────────

def chat_with_rag(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
) -> str:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=_SYSTEM_CACHED,
        messages=_build_messages(
            pregunta, doc_context, memory_context, business_context, learnings_context
        ),
    )
    return response.content[0].text


# ── Streaming ─────────────────────────────────────────────────────────────────

async def stream_chat_with_rag(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
):
    async with async_client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=_SYSTEM_CACHED,
        messages=_build_messages(
            pregunta, doc_context, memory_context, business_context, learnings_context
        ),
    ) as stream:
        async for text in stream.text_stream:
            yield text


# ── Utilidades ────────────────────────────────────────────────────────────────

def analyze_document(text: str, instruccion: str = "") -> str:
    prompt = f"""Analiza el siguiente documento empresarial y extrae:
1. Puntos clave y conclusiones principales
2. Datos financieros o métricas relevantes (si existen)
3. Riesgos u oportunidades identificados
4. Recomendaciones de acción

{f'Instrucción específica: {instruccion}' if instruccion else ''}

DOCUMENTO:
{text[:8000]}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=_SYSTEM_CACHED,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def extract_learning(question: str, answer: str) -> tuple[str, str]:
    prompt = f"""De esta conversación, extrae en formato JSON:
{{"tema": "tema principal en 3-5 palabras", "insight": "aprendizaje clave en 1 oración"}}

Pregunta: {question}
Respuesta: {answer[:300]}

Responde SOLO con el JSON, sin texto adicional."""

    try:
        import json
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(response.content[0].text.strip())
        return data.get("tema", "general"), data.get("insight", "")
    except Exception:
        return "general", ""
