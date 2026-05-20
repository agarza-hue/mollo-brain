"""
Cerebro OpenAI de Mollo — GPT-4o-mini para queries simples, GPT-4o para medias.
Misma personalidad y herramientas que Claude, pero mucho más barato.
"""
from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

GPT_MINI = "gpt-4o-mini"
GPT_4O   = "gpt-4o"


def _cached_tokens(usage) -> int:
    """OpenAI auto-caches stable prefixes ≥1024 tok since Q4 2024.
    Cached tokens are billed at 50% off and reported under
    `usage.prompt_tokens_details.cached_tokens`. Returns 0 if absent."""
    details = getattr(usage, "prompt_tokens_details", None)
    return getattr(details, "cached_tokens", 0) or 0


def _split_input(usage) -> tuple[int, int]:
    """Returns (non_cached_input, cache_read).

    OpenAI's `prompt_tokens` INCLUDES cached tokens (unlike Anthropic, where
    `input_tokens` excludes them). To match the cost_service convention —
    which expects `input_tokens` to mean "tokens charged at full price" —
    we subtract the cached portion before reporting."""
    cached = _cached_tokens(usage)
    full = usage.prompt_tokens or 0
    return max(0, full - cached), cached

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

══════════════════════════════════════════════════════════════════════
CONTEXTO DE NEGOCIO DE ADOLFO
══════════════════════════════════════════════════════════════════════
Adolfo opera múltiples emprendimientos en paralelo. Cuando responda, considerá cuál aplica:
- **MolloIA** (`app.mollo-ai.com`): SaaS multi-tenant de IA con routing inteligente. Comercializando ahora a $19/mes Pro y $49/asiento Team
- **Vantamedia** (`vanta_project`): plataforma financiera SPA con parser Excel
- **SinergyOS:** consultoría de transformación digital, servicios B2B
- **Strategy OS:** herramienta de planeación estratégica para clientes
- **IPTV** y **Excel RE Platform:** infraestructura técnica
Trabaja desde Monterrey, Nuevo León, México.

══════════════════════════════════════════════════════════════════════
FORMATO DE RESPUESTA (uso flexible — adapta al tipo de query)
══════════════════════════════════════════════════════════════════════
**Para queries operacionales rápidas** (1-3 líneas):
  Respuesta directa. Sin secciones. Sin disclaimers.

**Para queries estratégicas/análisis** (estructura completa):
  1. **Respuesta directa:** lo que Adolfo necesita saber primero
  2. **Análisis:** el por qué, contexto, datos relevantes
  3. **Acción recomendada:** pasos concretos en orden
  4. **Consideraciones:** riesgos, alternativas, qué validar

**Para queries técnicas/código:**
  - Snippets concretos en code blocks con lenguaje correcto
  - Comandos copy-paste listos para terminal
  - Si hay tradeoff de implementación, márcalo explícito

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
- Si la pregunta menciona un documento por nombre y no llegó en contexto: explícalo, no inventes
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
- Si detectás contradicción en la query del user con datos previos: levantá la mano, no asumas"""

# Contexto VPS solo para modo agente — no enviarlo en queries simples/medias.
# Mantén esto al MÁXIMO 300 tok: detalles específicos se descubren via tools
# (estado_vps, bash ls). Cualquier edit invalida el prompt cache hasta que
# se materializa la próxima entrada (~30s).
_VPS_CONTEXT = """

━━ MODO DESARROLLADOR — VPS de Adolfo (79.143.94.153) ━━

Si Adolfo pide cambios en cualquier proyecto:
- bash / leer_archivo / escribir_archivo para editar y desplegar
- estado_vps para inspeccionar containers/servicios (preferir sobre asumir)
- SIEMPRE leer antes de editar; SIEMPRE verificar con logs antes de declarar éxito

PROYECTOS (paths absolutos):
- /root/mollo_brain/             FastAPI :8002 — systemd `mollo-brain` (este servidor)
- /root/projects/mollo-os/       Next.js dev :3006 — público https://app.mollo-ai.com
- /root/projects/juntas-app/     juntas_nginx :80/:443 — sirve TLS para todos los dominios
- /root/strategy_os/             Strategy OS (Appsmith :8080)
- /root/excel_platform/          Excel RE Platform (FastAPI :8010)
- /root/vanta_project/           Vantamedia (nginx :8090)
- /var/www/mollo-ai/landing/     landing brand v2 (servida por juntas_nginx)

DOMINIOS:
- app.mollo-ai.com   → Mollo OS (TLS via juntas_nginx)
- landing.mollo-ai.com → landing brand v2
- mollo-ai.com (apex) → AWS (sitio viejo, fuera de este VPS)

DEPLOY PATTERNS:
- systemd (mollo-brain etc):  systemctl restart <servicio>
- Docker compose:             docker compose up -d <svc>  ó  docker restart <name>
- Next.js dev (mollo-os):     hot-reload, no build necesario

LOGS:
- mollo-brain:  journalctl -u mollo-brain -n 100  ó  /var/log/mollo_brain.log
- container:    docker logs <name> --tail 100
- Next dev:     /tmp/mollo-dev.log

NOTA: el bind-mount de archivo individual (juntas_nginx default.conf) se rompe
con Edit del host (atomic rename cambia inode). Después de editar, `docker
restart juntas_nginx` para resincronizar."""

_TOOL_USE_DIRECTIVE = """

REGLA CRÍTICA DE TOOL USE:
- Si el usuario menciona una ruta de archivo (ej. /root/..., /var/..., .py, .md, .json), USA `leer_archivo`. NO inventes contenido.
- Si pide ejecutar, reiniciar o ver estado de un servicio: USA `bash` o `estado_vps`.
- Si pide buscar en internet o info actual: USA `buscar_web`.
- Si pide modificar/crear un archivo: USA `escribir_archivo`.
- Si NO estás 100% seguro del contenido de algo concreto: USA la tool relevante en lugar de adivinar.
- Solo responde de memoria cuando la pregunta es conceptual o no referencia algo específico del sistema."""

MOLLO_SYSTEM_AGENT = MOLLO_SYSTEM + _VPS_CONTEXT + _TOOL_USE_DIRECTIVE

MAX_AGENT_ITERATIONS = 8

# Output caps por intent — la mayoría de queries son análisis cortos.
# Mini queda en 1024 (suficiente). gpt-4o default 1500 (antes 4096) con
# expansión a 4096 sólo para deliverables largos.
GPT4O_DEFAULT_MAX  = 1500
GPT4O_LONGFORM_MAX = 4096
MINI_MAX           = 1024  # mini siempre cap bajo

_LONG_FORM_KEYWORDS = (
    "manual", "documento", "playbook", "guía", "framework completo",
    "redacta", "elabora", "diseña un plan", "diseña una estrategia",
    "ensayo", "propuesta detallada", "memorándum", "informe ejecutivo",
    "deck", "presentación", "okrs completos", "iso 9001 procedimiento",
    "contrato", "términos y condiciones",
)


def _max_tokens_for(question: str, model: str) -> int:
    """Mini siempre 1024. gpt-4o: default 1500, long-form 4096."""
    if model == GPT_MINI:
        return MINI_MAX
    q = (question or "").lower()
    if any(kw in q for kw in _LONG_FORM_KEYWORDS) or len(q.split()) > 120:
        return GPT4O_LONGFORM_MAX
    return GPT4O_DEFAULT_MAX


def _system_with_user_md(base_system: str) -> str:
    """Prepende CLAUDE.md (contexto personal del user) al system prompt.
    OpenAI auto-cachea prefijos idénticos ≥1024 tok — al ser CLAUDE.md
    estable durante 5min (TTL), el cache se mantiene caliente entre requests."""
    try:
        from user_context_service import get_user_claude_md_section
        section = get_user_claude_md_section()
        if section:
            return f"{base_system}\n\n{section}"
    except Exception:
        pass
    return base_system


def _build_messages(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    system_prompt: str | None = None,
) -> list[dict]:
    # ORDEN CRÍTICO para prompt caching: bloques INVARIANTES primero,
    # variables después. OpenAI auto-cachea prefijos idénticos ≥1024 tok;
    # cualquier variación en posición temprana invalida el cache. Por eso
    # business/learnings (estables) van primero, topic_memory/memory_context
    # (varían por query) y doc_context/pregunta (siempre cambian) al final.
    parts = []
    if business_context:
        parts.append(f"CONTEXTO DEL NEGOCIO:\n{business_context}")
    if learnings_context:
        parts.append(f"APRENDIZAJES:\n{learnings_context}")
    # ── divisor: lo de arriba es estable, lo de abajo varía ──
    if topic_memory:
        parts.append(f"MEMORIA POR TEMAS:\n{topic_memory}")
    if memory_context:
        parts.append(f"CONVERSACIONES RECIENTES:\n{memory_context}")
    if doc_context:
        parts.append(f"DOCUMENTOS RELEVANTES:\n{doc_context}")
    parts.append(f"PREGUNTA: {pregunta}")

    final_system = _system_with_user_md(system_prompt or MOLLO_SYSTEM)
    return [
        {"role": "system", "content": final_system},
        {"role": "user",   "content": "\n\n".join(parts)},
    ]


def _build_agent_messages(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
) -> list[dict]:
    """Igual que _build_messages pero con contexto VPS para modo agente."""
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

    final_system = _system_with_user_md(MOLLO_SYSTEM_AGENT)
    return [
        {"role": "system", "content": final_system},
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
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    max_tok = _max_tokens_for(pregunta, model)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tok,
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory, system_prompt,
        ),
    )
    in_tok, cached = _split_input(response.usage)
    usage = {
        "input_tokens":      in_tok,
        "output_tokens":     response.usage.completion_tokens,
        "cache_read_tokens": cached,
        "model": model,
    }
    return response.choices[0].message.content, usage


async def stream_chat_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_MINI,
    system_prompt: str | None = None,
):
    import json as _json
    max_tok = _max_tokens_for(pregunta, model)
    stream = client.chat.completions.create(
        model=model,
        max_tokens=max_tok,
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
            in_tok, cached = _split_input(chunk.usage)
            usage = {
                "input_tokens":      in_tok,
                "output_tokens":     chunk.usage.completion_tokens,
                "cache_read_tokens": cached,
                "model": model,
            }
            yield f"\x03{_json.dumps(usage)}"


# ── Agentic loop con herramientas ─────────────────────────────────────────────

async def run_agent_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_4O,
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    from tools_service import select_tools, execute_tool

    messages     = _build_agent_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    # Lazy-load: pick only the tools whose keywords match the question.
    # Always includes Tier 1 (bash, leer/escribir_archivo, buscar_web, estado_vps).
    openai_tools = _claude_tools_to_openai(select_tools(pregunta))
    total_input = total_output = total_cached = 0

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            tools=openai_tools,
            tool_choice="auto",
            messages=messages,
        )
        in_tok, cached = _split_input(response.usage)
        total_input  += in_tok
        total_output += response.usage.completion_tokens
        total_cached += cached
        msg = response.choices[0].message

        if response.choices[0].finish_reason == "stop":
            break

        if response.choices[0].finish_reason == "tool_calls" and msg.tool_calls:
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
            break

    usage = {"input_tokens": total_input, "output_tokens": total_output,
             "cache_read_tokens": total_cached, "model": model}
    return msg.content or "Agente alcanzó el límite de iteraciones.", usage


async def stream_agent_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_4O,
    system_prompt: str | None = None,
):
    import json as _json
    from tools_service import select_tools, execute_tool, begin_tool_events, drain_tool_events

    messages     = _build_agent_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    openai_tools = _claude_tools_to_openai(select_tools(pregunta))
    total_input = total_output = total_cached = 0
    begin_tool_events()  # buffer para eventos estructurados (writes con diff, etc.)

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            tools=openai_tools,
            tool_choice="auto",
            messages=messages,
        )
        in_tok, cached = _split_input(response.usage)
        total_input  += in_tok
        total_output += response.usage.completion_tokens
        total_cached += cached
        msg    = response.choices[0].message
        reason = response.choices[0].finish_reason

        if reason == "stop":
            yield msg.content or ""
            break

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
                # Drenar eventos estructurados que la tool haya emitido
                for ev in drain_tool_events():
                    yield f"\x05{_json.dumps(ev)}\n"
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result),
                })
        else:
            yield msg.content or ""
            break

    usage = {"input_tokens": total_input, "output_tokens": total_output,
             "cache_read_tokens": total_cached, "model": model}
    yield f"\x03{_json.dumps(usage)}"
