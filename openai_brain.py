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

EXPERTISE: Finanzas · Estrategia · RRHH · Ventas · PMO · ISO 9001 · VPS · IA aplicada · Desarrollo de software

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

# Contexto VPS solo para modo agente — no enviarlo en queries simples/medias.
# Ahorra ~2,100 tokens por request en modos simple/medio.
_VPS_CONTEXT = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MODO DESARROLLADOR — VPS COMPLETO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Eres el desarrollador full-stack del VPS de Adolfo (79.143.94.153).
Cuando pida cambios en cualquier proyecto, usa bash/leer_archivo/escribir_archivo.
SIEMPRE lee el archivo antes de editarlo. SIEMPRE verifica el deploy con logs.

━━ PROYECTOS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[MolloAI Web] /root/projects/mollo-web/
  Stack: Next.js 15 · React 19 · TypeScript · Tailwind · App Router
  Puerto: 3001 (PM2: "mollo-web") → Nginx /mollo/
  Deploy: cd /root/projects/mollo-web && npm run build → pm2 restart mollo-web
  Clave: basePath='/mollo', dynamic(ssr:false) para evitar hydration
  API routes: /api/chat → 8002/chat, /api/convs → 8002/convs
  Archivos clave:
    app/chat-client.tsx      → UI principal, streaming, upload, convs
    app/lib/auth.ts          → JWT helpers (getToken/setAuth/clearAuth)
    app/api/*/route.ts       → proxies al brain

[Mollo Brain] /root/mollo_brain/
  Stack: FastAPI · Python 3.11 · uvicorn · Qdrant · OpenAI/Claude
  Puerto: 8002 (proceso uvicorn directo, no Docker)
  Deploy: kill $(lsof -ti:8002) && sleep 1 && nohup /root/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8002 --workers 2 > /tmp/brain.log 2>&1 &
  Logs: tail -50 /tmp/mollo_brain.log

[Juntas App] /root/projects/juntas-app/
  Stack: Next.js · Prisma · PostgreSQL · Docker Compose
  Puerto: 80 (Docker: juntas_nginx → juntas_app:3000)
  Deploy: docker compose build app && docker compose up -d app

[Strategy OS] /root/strategy_os/
  Stack: Appsmith (port 8080) + FastAPI backend + PostgreSQL

[Mollo Gateway] /opt/mollo-gateway/ — Puerto: 8100

━━ INFRAESTRUCTURA ━━━━━━━━━━━━━━━━━━━━━━━

Docker: juntas_nginx:80/443 · juntas_app:3000 · molloai_postgres:5434
        strategy_postgres:5432 · qdrant:6333 · strategy_n8n:5678
        strategy_appsmith:8080 · iptv_postgres:5432 · iptv_redis:6379
Nginx: /mollo/ → localhost:3001 | / → juntas_app:3000
PM2: pm2 list | pm2 logs <nombre> --lines N --nostream
Venv: /root/venv/bin/python

━━ FLUJO DE TRABAJO ━━━━━━━━━━━━━━━━━━━━━━

1. bash "ls /ruta" → entender estructura
2. leer_archivo "/ruta/archivo" → ver código actual
3. escribir_archivo "/ruta/archivo" con contenido nuevo completo
4. bash deploy command → reiniciar servicio
5. bash "pm2 logs ... --nostream" → verificar

━━ CONVENCIONES ━━━━━━━━━━━━━━━━━━━━━━━━━

Frontend: #1a1410 bg · amber-500 accent · 'use client' + dynamic(ssr:false)
Backend: async para I/O · imports lazy · sin comentarios obvios"""

MOLLO_SYSTEM_AGENT = MOLLO_SYSTEM + _VPS_CONTEXT

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

    return [
        {"role": "system", "content": MOLLO_SYSTEM_AGENT},
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
) -> tuple[str, dict]:
    # mini responde corto; 4o puede necesitar más espacio para análisis
    max_tok = 1024 if model == GPT_MINI else 4096
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tok,
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory,
        ),
    )
    usage = {
        "input_tokens":  response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "cache_read_tokens": 0,
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
):
    import json as _json
    max_tok = 1024 if model == GPT_MINI else 4096
    stream = client.chat.completions.create(
        model=model,
        max_tokens=max_tok,
        stream=True,
        stream_options={"include_usage": True},
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory,
        ),
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta
        if chunk.usage:
            usage = {
                "input_tokens":      chunk.usage.prompt_tokens,
                "output_tokens":     chunk.usage.completion_tokens,
                "cache_read_tokens": 0,
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
) -> tuple[str, dict]:
    from tools_service import TOOLS, execute_tool

    messages     = _build_agent_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    openai_tools = _claude_tools_to_openai(TOOLS)
    total_input = total_output = 0

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            tools=openai_tools,
            tool_choice="auto",
            messages=messages,
        )
        total_input  += response.usage.prompt_tokens
        total_output += response.usage.completion_tokens
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
             "cache_read_tokens": 0, "model": model}
    return msg.content or "Agente alcanzó el límite de iteraciones.", usage


async def stream_agent_openai(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = GPT_4O,
):
    import json as _json
    from tools_service import TOOLS, execute_tool

    messages     = _build_agent_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    openai_tools = _claude_tools_to_openai(TOOLS)
    total_input = total_output = 0

    for _ in range(MAX_AGENT_ITERATIONS):
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            tools=openai_tools,
            tool_choice="auto",
            messages=messages,
        )
        total_input  += response.usage.prompt_tokens
        total_output += response.usage.completion_tokens
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
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result),
                })
        else:
            yield msg.content or ""
            break

    usage = {"input_tokens": total_input, "output_tokens": total_output,
             "cache_read_tokens": 0, "model": model}
    yield f"\x03{_json.dumps(usage)}"
