"""
Cerebro OpenAI de Mollo — GPT-4o-mini para queries simples, GPT-4o para medias.
Misma personalidad y herramientas que Claude, pero mucho más barato.
"""
from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

GPT_MINI = "gpt-4o-mini"
GPT_4O   = "gpt-4o"

MOLLO_SYSTEM = """Eres Mollo, el asistente ejecutivo personal de Adolfo. Nombrado así por su perro.

IDENTIDAD:
- Ejecutivo de alto nivel: directo, práctico, sin rodeos
- Hablas en español, sin errores ortográficos ni frases incompletas
- Pensamiento estratégico y analítico
- Proactivo: anticipas necesidades, identificas riesgos y oportunidades

EXPERTISE: Finanzas · Estrategia · RRHH · Ventas · PMO · ISO 9001 · VPS · IA aplicada

FORMATO DE RESPUESTA:
1. Respuesta directa (lo que Adolfo necesita saber YA)
2. Análisis (por qué y contexto, si aplica)
3. Acción recomendada (pasos concretos)
4. Consideraciones (riesgos, alternativas)

CUANDO USAS HERRAMIENTAS:
- Ejecuta primero, explica después
- Para buscar_web usa queries en inglés con términos precisos
- Informa el resultado de cada herramienta antes de continuar

REGLAS DE ORO:
- NUNCA dejes frases incompletas
- SIEMPRE termina con conclusión cerrada
- Si hay documentos de contexto, úsalos como base factual
- Si falta información crítica, pregunta específicamente"""

MAX_AGENT_ITERATIONS = 8


def _build_messages(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
) -> list[dict]:
    parts = []
    if business_context:
        parts.append(f"CONTEXTO DEL NEGOCIO DE ADOLFO:\n{business_context}")
    if topic_memory:
        parts.append(f"MEMORIA POR TEMAS:\n{topic_memory}")
    if learnings_context:
        parts.append(f"APRENDIZAJES GENERALES:\n{learnings_context}")
    if memory_context:
        parts.append(f"CONVERSACIONES RECIENTES:\n{memory_context}")
    if doc_context:
        parts.append(f"DOCUMENTOS RELEVANTES:\n{doc_context}")
    parts.append(f"PREGUNTA DE ADOLFO: {pregunta}")

    return [
        {"role": "system", "content": MOLLO_SYSTEM},
        {"role": "user",   "content": "\n\n".join(parts)},
    ]


def _claude_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convierte herramientas de formato Claude a formato OpenAI."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t["description"],
                "parameters":  t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


# ── Chat simple (sin herramientas) ────────────────────────────────────────────

def chat_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_MINI,
) -> str:
    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory,
        ),
    )
    return response.choices[0].message.content


async def stream_chat_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_MINI,
):
    stream = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        stream=True,
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory,
        ),
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# ── Agentic loop con herramientas ─────────────────────────────────────────────

async def run_agent_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_4O,
) -> str:
    from tools_service import TOOLS, execute_tool

    messages    = _build_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    openai_tools = _claude_tools_to_openai(TOOLS)

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            tools=openai_tools,
            tool_choice="auto",
            messages=messages,
        )
        msg = response.choices[0].message

        if response.choices[0].finish_reason == "stop":
            return msg.content or ""

        if response.choices[0].finish_reason == "tool_calls" and msg.tool_calls:
            # Serializar el mensaje del asistente a dict explícito
            messages.append({
                "role":       "assistant",
                "content":    msg.content or "",
                "tool_calls": [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                import json
                inputs = json.loads(tc.function.arguments)
                result = await execute_tool(tc.function.name, inputs)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result),
                })
        else:
            return msg.content or ""

    return "Agente alcanzó el límite de iteraciones."


async def stream_agent_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_4O,
):
    from tools_service import TOOLS, execute_tool

    messages     = _build_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    openai_tools = _claude_tools_to_openai(TOOLS)

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            tools=openai_tools,
            tool_choice="auto",
            messages=messages,
        )
        msg    = response.choices[0].message
        reason = response.choices[0].finish_reason

        if reason == "stop":
            yield msg.content or ""
            return

        if reason == "tool_calls" and msg.tool_calls:
            messages.append({
                "role":       "assistant",
                "content":    msg.content or "",
                "tool_calls": [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                import json
                inputs = json.loads(tc.function.arguments)
                yield f"\n_🔧 Ejecutando: {tc.function.name}…_\n"
                result = await execute_tool(tc.function.name, inputs)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result),
                })
        else:
            yield msg.content or ""
            return
