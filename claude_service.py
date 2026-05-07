"""Claude como cerebro principal de Mollo — RAG + agentic loop + prompt caching."""
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

client       = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
async_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

MOLLO_SYSTEM = """Eres Mollo, el asistente ejecutivo personal de Adolfo. Nombrado así por su perro.

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

CUANDO USAS HERRAMIENTAS:
- Ejecuta primero, explica después
- Para buscar_web usa queries en inglés con términos precisos. Ejemplo: en vez de "precio dólar hoy" usa "USD MXN exchange rate today" o "dollar peso Mexico today"
- Informa el resultado de cada herramienta antes de continuar
- Si una herramienta falla, dilo claramente y propón alternativa

REGLAS DE ORO:
- NUNCA dejes frases incompletas
- SIEMPRE termina con conclusión cerrada
- Si hay documentos de contexto, úsalos como base factual
- Cita la fuente del documento cuando usas información específica
- Si falta información crítica, pregunta específicamente
"""

_SYSTEM_CACHED = [
    {
        "type": "text",
        "text": MOLLO_SYSTEM,
        "cache_control": {"type": "ephemeral"},
    }
]

MAX_AGENT_ITERATIONS = 8


def _build_messages(
    pregunta: str,
    doc_context: str,
    memory_context: str,
    business_context: str,
    learnings_context: str,
    topic_memory: str = "",
) -> list[dict]:
    content = []

    # Bloque 1 (cacheado): contexto estático del negocio + temas especializados
    static_parts = []
    if business_context:
        static_parts.append(f"CONTEXTO DEL NEGOCIO DE ADOLFO:\n{business_context}")
    if topic_memory:
        static_parts.append(f"MEMORIA POR TEMAS (lo que Mollo recuerda de cada especialidad):\n{topic_memory}")
    if learnings_context:
        static_parts.append(f"APRENDIZAJES GENERALES:\n{learnings_context}")

    if static_parts:
        content.append({
            "type": "text",
            "text": "\n\n".join(static_parts),
            "cache_control": {"type": "ephemeral"},
        })

    # Bloque 2 (dinámico): conversación reciente + docs + pregunta actual
    dynamic_parts = []
    if memory_context:
        dynamic_parts.append(f"CONVERSACIONES RECIENTES:\n{memory_context}")
    if doc_context:
        dynamic_parts.append(f"DOCUMENTOS RELEVANTES:\n{doc_context}")
    dynamic_parts.append(f"PREGUNTA DE ADOLFO: {pregunta}")

    content.append({"type": "text", "text": "\n\n".join(dynamic_parts)})
    return [{"role": "user", "content": content}]


# ── Chat con RAG (sin herramientas — queries de conocimiento) ─────────────────

def chat_with_rag(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
) -> tuple[str, dict]:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8096,
        system=_SYSTEM_CACHED,
        messages=_build_messages(
            pregunta, doc_context, memory_context, business_context, learnings_context, topic_memory
        ),
    )
    usage = {
        "input_tokens":      response.usage.input_tokens,
        "output_tokens":     response.usage.output_tokens,
        "cache_read_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
        "model": CLAUDE_MODEL,
    }
    return response.content[0].text, usage


async def stream_chat_with_rag(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
):
    import json as _json
    async with async_client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=8096,
        system=_SYSTEM_CACHED,
        messages=_build_messages(
            pregunta, doc_context, memory_context, business_context, learnings_context, topic_memory
        ),
    ) as stream:
        async for text in stream.text_stream:
            yield text
        final = await stream.get_final_message()
        usage = {
            "input_tokens":      final.usage.input_tokens,
            "output_tokens":     final.usage.output_tokens,
            "cache_read_tokens": getattr(final.usage, "cache_read_input_tokens", 0),
            "model": CLAUDE_MODEL,
        }
        yield f"\x03{_json.dumps(usage)}"


# ── Agentic loop (con herramientas) ──────────────────────────────────────────

async def run_agent(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
) -> str:
    from tools_service import TOOLS, execute_tool

    messages = _build_messages(
        pregunta, doc_context, memory_context, business_context, learnings_context, topic_memory
    )

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8096,
            system=_SYSTEM_CACHED,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            return "\n".join(text_blocks) if text_blocks else "(sin respuesta)"

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "Agente alcanzó el límite de iteraciones sin conclusión."


async def stream_agent(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
):
    """Streaming real por token + progreso de herramientas.

    Cada iteración abre un stream async: los tokens de texto llegan en tiempo real
    al cliente mientras el modelo "piensa". Cuando Claude decide usar una herramienta
    se muestra el nombre, se ejecuta, y el loop continúa con el resultado.
    """
    import json as _json
    from tools_service import TOOLS, execute_tool

    messages = _build_messages(
        pregunta, doc_context, memory_context, business_context, learnings_context, topic_memory
    )
    total_input = total_output = 0

    for _ in range(MAX_AGENT_ITERATIONS):
        async with async_client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=8096,
            system=_SYSTEM_CACHED,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            # Entregar tokens de texto en tiempo real
            async for text in stream.text_stream:
                yield text

            # Esperar el mensaje completo para leer tool_use blocks y usage
            final = await stream.get_final_message()

        total_input  += final.usage.input_tokens
        total_output += final.usage.output_tokens

        if final.stop_reason == "end_turn":
            break

        if final.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": final.content})

            tool_results = []
            for block in final.content:
                if block.type == "tool_use":
                    yield f"\n_🔧 {block.name}…_\n"
                    result = await execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    usage = {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": getattr(final.usage, "cache_read_input_tokens", 0),
        "model": CLAUDE_MODEL,
    }
    yield f"\x03{_json.dumps(usage)}"


# ── Utilidades ────────────────────────────────────────────────────────────────

def analyze_document(text: str, instruccion: str = "") -> tuple[str, dict]:
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
        max_tokens=4096,
        system=_SYSTEM_CACHED,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = {
        "input_tokens":      response.usage.input_tokens,
        "output_tokens":     response.usage.output_tokens,
        "cache_read_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
        "model": CLAUDE_MODEL,
    }
    return response.content[0].text, usage
