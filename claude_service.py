"""Claude como cerebro principal de Mollo — RAG + análisis + memoria."""
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


def chat_with_rag(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
) -> str:
    """Genera respuesta de Mollo con contexto RAG + memoria + aprendizajes."""

    user_message = _build_user_message(
        pregunta, doc_context, memory_context, business_context, learnings_context
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=MOLLO_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def _build_user_message(
    pregunta: str,
    doc_context: str,
    memory_context: str,
    business_context: str,
    learnings_context: str,
) -> str:
    parts = []
    if business_context:
        parts.append(f"CONTEXTO DEL NEGOCIO DE ADOLFO:\n{business_context}")
    if learnings_context:
        parts.append(f"APRENDIZAJES PREVIOS DE MOLLO:\n{learnings_context}")
    if memory_context:
        parts.append(f"CONVERSACIONES RECIENTES:\n{memory_context}")
    if doc_context:
        parts.append(f"DOCUMENTOS RELEVANTES ENCONTRADOS:\n{doc_context}")
    full = "\n\n".join(parts)
    return f"{full}\n\nPREGUNTA DE ADOLFO: {pregunta}" if full else pregunta


async def stream_chat_with_rag(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
):
    """Genera respuesta de Mollo en streaming, token a token."""
    user_message = _build_user_message(
        pregunta, doc_context, memory_context, business_context, learnings_context
    )
    async with async_client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=MOLLO_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        async for text in stream.text_stream:
            yield text


def analyze_document(text: str, instruccion: str = "") -> str:
    """Analiza un documento específico y extrae insights."""
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
        system=MOLLO_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def extract_learning(question: str, answer: str) -> tuple[str, str]:
    """Extrae tema e insight de una conversación para la memoria."""
    prompt = f"""De esta conversación, extrae en formato JSON:
{{"tema": "tema principal en 3-5 palabras", "insight": "aprendizaje clave en 1 oración"}}

Pregunta: {question}
Respuesta: {answer[:300]}

Responde SOLO con el JSON, sin texto adicional."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        data = json.loads(response.content[0].text.strip())
        return data.get("tema", "general"), data.get("insight", "")
    except Exception:
        return "general", ""
