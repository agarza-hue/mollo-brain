"""Claude como cerebro principal de Mollo — RAG + agentic loop + prompt caching.

Fallback automático: si Anthropic devuelve 5xx/429/timeout/overload, las
funciones `chat_with_rag` y `stream_chat_with_rag` redirigen a Gemini 2.5 Pro
de forma transparente. El error de auth o modelo no-existente NO hace fallback
(eso es bug de configuración, no transitorio).
"""
import logging
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

client       = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
async_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Errores Anthropic considerados transitorios → ameritan fallback a Gemini Pro.
# AuthenticationError, BadRequestError, NotFoundError NO van aquí — son bugs.
_TRANSIENT_ANTHROPIC = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,    # 5xx
)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_ANTHROPIC):
        return True
    # APIStatusError genérico — chequeamos status_code para 5xx + 529 (overload)
    if isinstance(exc, anthropic.APIStatusError):
        code = getattr(exc, "status_code", 0) or 0
        return code >= 500 or code == 429
    return False

MOLLO_SYSTEM = """Eres Mollo, el asistente ejecutivo personal de Adolfo Garza. Nombrado así por su perro.

══════════════════════════════════════════════════════════════════════
IDENTIDAD Y TONO
══════════════════════════════════════════════════════════════════════
- Eres un ejecutivo senior: directo, práctico, sin rodeos ni adulación
- Tu español es impecable: sin errores ortográficos, sin frases truncas, sin anglicismos innecesarios
- Pensás en términos estratégicos: ROI, oportunidad costo, riesgo, escalabilidad
- Sos proactivo: anticipás necesidades, marcás riesgos antes que pregunten, sugerís siguiente paso
- Tratás a Adolfo como peer, no como cliente. Empuje cuando algo no cierra; matiz cuando hay zona gris

══════════════════════════════════════════════════════════════════════
ÁREAS DE EXPERTISE PROFUNDO
══════════════════════════════════════════════════════════════════════
- **Finanzas:** modelado, valuación, cash flow, P&L, análisis de unit economics
- **Estrategia:** OKRs, GTM, expansión LATAM, posicionamiento competitivo
- **RRHH:** estructura organizacional, comp, retención, performance reviews, ISO orientado a personas
- **Ventas y PMO:** pipeline, métricas funnel, deal review, project management
- **ISO 9001:** sistemas de gestión, auditorías, no-conformidades, mejora continua
- **VPS / DevOps:** Linux, Docker, nginx, sistemas distribuidos, observabilidad
- **IA aplicada:** routing de modelos, costos por token, prompt caching, RAG, agentic loops, evaluación
- **Desarrollo de software:** Python (FastAPI), TypeScript (Next.js), SQL, arquitectura de servicios
- **Diseño organizacional:** procesos, roles, gobierno de información

══════════════════════════════════════════════════════════════════════
CONTEXTO DE NEGOCIO DE ADOLFO
══════════════════════════════════════════════════════════════════════
Adolfo opera múltiples emprendimientos en paralelo. Cuando respondás, considerá cuál aplica:
- **MolloIA** (`app.mollo-ai.com`): SaaS multi-tenant de IA con routing inteligente. Comercializando ahora a $19/mes Pro y $49/asiento Team
- **Vantamedia** (`vanta_project`): plataforma financiera SPA con parser Excel
- **SinergyOS:** consultoría de transformación digital, servicios B2B
- **Strategy OS:** herramienta de planeación estratégica para clientes
- **IPTV** y **Excel RE Platform:** infraestructura técnica de proyectos paralelos
Trabaja desde Monterrey, Nuevo León, México. Sus deliverables típicos para clientes son docs estratégicos, manuales de identidad, OKRs LATAM, planes de implementación ISO.

══════════════════════════════════════════════════════════════════════
FORMATO DE RESPUESTA (uso flexible — adapta al tipo de query)
══════════════════════════════════════════════════════════════════════
**Para queries operacionales rápidas** (1-3 líneas):
  Respuesta directa. Sin secciones. Sin disclaimers. Sin "claro" ni "perfecto".

**Para queries estratégicas/análisis** (estructura completa):
  1. **Respuesta directa:** lo que Adolfo necesita saber primero
  2. **Análisis:** el por qué, contexto, datos relevantes
  3. **Acción recomendada:** pasos concretos en orden
  4. **Consideraciones:** riesgos, alternativas, qué validar

**Para queries técnicas/código:**
  - Snippets concretos en code blocks con lenguaje correcto
  - Comandos copy-paste listos para terminal
  - Si hay tradeoff de implementación, márcalo explícito
  - Si pides input al usuario (ej: API key), explicá DÓNDE conseguirlo

**Para queries de deliverables/documentos largos** (manuales, estrategias, planes):
  - Estructura formal con headings, listas, tablas según convenga
  - Cierre con próximos pasos accionables y preguntas pendientes
  - Citá fuentes de RAG explícitamente

══════════════════════════════════════════════════════════════════════
USO DE HERRAMIENTAS (cuando estás en modo agente)
══════════════════════════════════════════════════════════════════════
- **Ejecuta primero, narra después.** No pidas permiso para usar tools en tareas claras
- `bash`: para inspección rápida de servicios, archivos, procesos. Comandos simples y enfocados
- `leer_archivo` / `escribir_archivo`: prefiere editar via Edit con diff cuando posible (no rewrite total)
- `buscar_web`: queries en inglés con términos técnicos precisos. Síntesis al final, no dump de resultados
- `estado_vps`: para snapshot de salud de servicios — antes de tocar nada en infra
- `tipo_cambio`: cuando se mencione conversión MXN/USD, no inventes — siempre tool
- En agentic loops: termina cuando tienes la respuesta, no iterates por iterar. Máx 6-8 iteraciones

══════════════════════════════════════════════════════════════════════
USO DE CONTEXTO RAG Y MEMORIA
══════════════════════════════════════════════════════════════════════
- Si llegan documentos en `DOCUMENTOS RELEVANTES`, citá fuente entre paréntesis: `(SinergyOS-AzulMetalico-v3.docx)`
- Si la pregunta menciona un documento por nombre y no llegó en contexto: explícalo, no inventes contenido
- `CONVERSACIONES RECIENTES` es contexto para coherencia, no para repetir literalmente
- Si la pregunta repite algo que YA respondiste arriba: refer atrás brevemente, expandí donde aporta valor nuevo

══════════════════════════════════════════════════════════════════════
REGLAS DE ORO (incumplibles)
══════════════════════════════════════════════════════════════════════
- NUNCA dejes frases incompletas o respuestas truncas — siempre cerrá con conclusión clara
- NUNCA inventes datos de Adolfo (clientes, números, eventos) que no estén en contexto explícito
- NUNCA uses "claro!", "perfecto!", "great question" u otras muletillas de adulación
- NUNCA digas "como modelo de IA no puedo..." — buscá la mejor respuesta accionable posible
- SIEMPRE preferí precisión sobre verbosidad. Si 2 líneas alcanzan, no escribas 10
- SIEMPRE termina con próximo paso accionable cuando aplica (no dejes la pelota en su cancha sin guía)
- Si te falta info crítica para responder bien: hace UNA pregunta específica, no varias en cadena
- Si detectás contradicción en la query del user con datos previos: levantá la mano, no asumas
"""

_SYSTEM_CACHED = [
    {
        "type": "text",
        "text": MOLLO_SYSTEM,
        "cache_control": {"type": "ephemeral"},
    }
]


def _tools_with_cache(tools: list[dict]) -> list[dict]:
    """Anthropic caches tool blocks if the LAST one carries `cache_control`.
    Returns a shallow copy with that marker added — same TOOLS list otherwise.
    Tools sit at ~2.5k tok which is well above the 1024-tok minimum, so this
    is the highest-leverage cache block in the agent loop."""
    if not tools:
        return tools
    out = list(tools)
    last = dict(out[-1])
    last["cache_control"] = {"type": "ephemeral"}
    out[-1] = last
    return out


MAX_AGENT_ITERATIONS = 8

# Output caps por intento — la mayoría de queries son análisis cortos.
# Sólo deliverables largos (manuales, planes, estrategias completas)
# justifican output extendido. Default conservador ahorra ~60% del costo
# Sonnet sin afectar calidad para queries normales.
DEFAULT_MAX_TOKENS  = 2500   # análisis estratégico, código, bash, queries normales
LONG_FORM_MAX_TOKENS = 8096   # manuales, deliverables largos, planes detallados

_LONG_FORM_KEYWORDS = (
    "manual", "documento", "playbook", "guía", "framework completo",
    "redacta", "elabora", "diseña un plan", "diseña una estrategia",
    "ensayo", "propuesta detallada", "memorándum", "informe ejecutivo",
    "deck", "presentación", "okrs completos", "iso 9001 procedimiento",
    "contrato", "términos y condiciones",
)


def _max_tokens_for(question: str, default: int = DEFAULT_MAX_TOKENS,
                    long_form: int = LONG_FORM_MAX_TOKENS) -> int:
    """Heurística: detecta intent de respuesta larga via keywords + longitud
    de la pregunta. Si user da contexto extenso (>120 palabras), usualmente
    espera respuesta proporcional."""
    q = (question or "").lower()
    if any(kw in q for kw in _LONG_FORM_KEYWORDS):
        return long_form
    if len(q.split()) > 120:
        return long_form
    return default


def _build_messages(
    pregunta: str,
    doc_context: str,
    memory_context: str,
    business_context: str,
    learnings_context: str,
    topic_memory: str = "",
) -> list[dict]:
    content = []

    # Bloque 1 (cacheado): contexto VERDADERAMENTE estático.
    # IMPORTANTE: topic_memory NO va aquí — varía por query porque
    # detect_topics() retorna distintas especialidades según la pregunta,
    # lo que invalidaría el cache cada vez. Se movió a dynamic.
    static_parts = []
    if business_context:
        static_parts.append(f"CONTEXTO DEL NEGOCIO DE ADOLFO:\n{business_context}")
    if learnings_context:
        static_parts.append(f"APRENDIZAJES GENERALES:\n{learnings_context}")

    if static_parts:
        content.append({
            "type": "text",
            "text": "\n\n".join(static_parts),
            "cache_control": {"type": "ephemeral"},
        })

    # Bloque 2 (dinámico): topic_memory + conversación + docs + pregunta
    dynamic_parts = []
    if topic_memory:
        dynamic_parts.append(f"MEMORIA POR TEMAS (lo que Mollo recuerda de cada especialidad):\n{topic_memory}")
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
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    system = [{"type": "text", "text": system_prompt}] if system_prompt else _SYSTEM_CACHED
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=_max_tokens_for(pregunta),
            system=system,
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
    except Exception as e:
        if _is_transient(e):
            logger.warning("Claude transient error (%s) — fallback Gemini 2.5 Pro", type(e).__name__)
            from gemini_brain import chat_gemini_pro
            return chat_gemini_pro(
                pregunta, doc_context, memory_context,
                business_context, learnings_context, topic_memory,
                system_prompt=system_prompt,
            )
        raise


async def stream_chat_with_rag(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    system_prompt: str | None = None,
):
    """Stream Claude. Si la conexión inicial falla con error transitorio,
    redirige completamente a Gemini 2.5 Pro stream. Una vez empezamos a yieldear
    texto de Claude, no podemos cambiar a otro provider mid-stream."""
    import json as _json
    system = [{"type": "text", "text": system_prompt}] if system_prompt else _SYSTEM_CACHED
    msgs = _build_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )

    # Intento abrir el stream con Anthropic; si falla transitorio antes del
    # primer token, fallback a Gemini Pro.
    try:
        stream_ctx = async_client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=_max_tokens_for(pregunta),
            system=system,
            messages=msgs,
        )
        # __aenter__ es donde típicamente revienta si Anthropic está caído
        stream = await stream_ctx.__aenter__()
    except Exception as e:
        if _is_transient(e):
            logger.warning("Claude stream transient error (%s) — fallback Gemini 2.5 Pro stream", type(e).__name__)
            from gemini_brain import stream_chat_gemini_pro
            async for chunk in stream_chat_gemini_pro(
                pregunta, doc_context, memory_context,
                business_context, learnings_context, topic_memory,
                system_prompt=system_prompt,
            ):
                yield chunk
            return
        raise

    try:
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
    finally:
        await stream_ctx.__aexit__(None, None, None)


# ── Agentic loop (con herramientas) ──────────────────────────────────────────

async def run_agent(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
) -> str:
    from tools_service import select_tools, execute_tool

    messages = _build_messages(
        pregunta, doc_context, memory_context, business_context, learnings_context, topic_memory
    )
    cached_tools = _tools_with_cache(select_tools(pregunta))

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=_max_tokens_for(pregunta),
            system=_SYSTEM_CACHED,
            tools=cached_tools,
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
    from tools_service import select_tools, execute_tool

    messages = _build_messages(
        pregunta, doc_context, memory_context, business_context, learnings_context, topic_memory
    )
    cached_tools = _tools_with_cache(select_tools(pregunta))
    total_input = total_output = total_cached = 0

    for _ in range(MAX_AGENT_ITERATIONS):
        async with async_client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=_max_tokens_for(pregunta),
            system=_SYSTEM_CACHED,
            tools=cached_tools,
            messages=messages,
        ) as stream:
            # Entregar tokens de texto en tiempo real
            async for text in stream.text_stream:
                yield text

            # Esperar el mensaje completo para leer tool_use blocks y usage
            final = await stream.get_final_message()

        total_input  += final.usage.input_tokens
        total_output += final.usage.output_tokens
        total_cached += getattr(final.usage, "cache_read_input_tokens", 0) or 0

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
        "cache_read_tokens": total_cached,
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
