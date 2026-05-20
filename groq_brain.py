"""
Groq Llama 3.3 70B — backend alternativo para modo `agente`.

Cuándo usarlo:
  - Queries agente donde la latencia importa (Groq 394 TPS vs gpt-4o ~120 TPS)
  - Volumen alto donde el costo por iteración pesa
  - Cuando OpenAI rate-limita

Trade-off vs gpt-4o:
  + Input $0.59/1M (76% menos)
  + Output $0.79/1M (92% menos)
  + Velocidad 3x
  − Sin prompt caching (perdemos el ~73% cache hit logrado en gpt-4o agente)
  − Tool use menos battle-tested (Llama 3.3 sí soporta function calling pero
    la calidad de selección de tools es inferior)
"""
import json as _json
import re as _re
from groq import (
    Groq,
    BadRequestError as GroqBadRequestError,
    RateLimitError as GroqRateLimitError,
)
from config import GROQ_API_KEY, LLAMA70B_MODEL
from openai_brain import (
    MOLLO_SYSTEM_AGENT, MAX_AGENT_ITERATIONS,
    _build_agent_messages, _claude_tools_to_openai,
)

# Llama emite tool calls como texto plano cuando falla el structured tool_use.
# Variantes vistas: `<function=NAME>{json}</function>`, `<function NAME>{json}</function>`,
# `<function\tNAME>{json}</function>`. El nombre va separado del literal `function`
# por `=`, espacio, tab o newline; el cuerpo es JSON entre el `>` final y `</function>`.
_INLINE_FUNCTION_RE = _re.compile(
    r"<function[\s=]+([a-zA-Z_][a-zA-Z0-9_]*)\s*>(.*?)</function>",
    _re.DOTALL,
)


def _parse_inline_function_calls(text: str) -> list[tuple[str, str]]:
    """Extrae (nombre, args_json) de tool calls embebidos como texto."""
    if not text or "<function" not in text:
        return []
    return [(m.group(1), m.group(2).strip()) for m in _INLINE_FUNCTION_RE.finditer(text)]


def _strip_inline_function_calls(text: str) -> str:
    """Quita los `<function ...>{...}</function>` del texto para no mostrarlos al usuario."""
    return _INLINE_FUNCTION_RE.sub("", text or "").strip()

LLAMA_70B = LLAMA70B_MODEL

_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


# Llama 3.3 70B en Groq es flaky con tool calls — a veces devuelve el call
# como string XML (`<function=bash{...}</function>`) en vez de JSON tool_calls
# estructurado. Groq retorna 400 `tool_use_failed`. Mensaje user-friendly:
_TOOL_USE_FAILED_MSG = (
    "⚠️ El modelo **Llama 3.3 70B** no logró formatear la llamada a herramientas. "
    "Esto pasa ~10-20% de las veces con este modelo en Groq (limitación conocida). "
    "Sugerencia: usa **Agente + tools** (GPT-4o) para esta query."
)

_RATE_LIMIT_MSG = (
    "⚠️ **Groq rate limit alcanzado** en el tier free (100K TPD para Llama 3.3 70B). "
    "Reintenta en unos minutos o usa **Agente + tools** (GPT-4o)."
)


def _usage_from_response(usage_obj, model: str) -> dict:
    """Groq usage: prompt_tokens, completion_tokens. Sin caching aún."""
    return {
        "input_tokens":      getattr(usage_obj, "prompt_tokens", 0) or 0,
        "output_tokens":     getattr(usage_obj, "completion_tokens", 0) or 0,
        "cache_read_tokens": 0,
        "model":             model,
    }


async def run_agent_groq(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = LLAMA_70B,
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    """Agentic loop con Groq Llama 3.3 70B. Misma signatura que run_agent_openai."""
    from tools_service import select_tools, execute_tool
    if _groq is None:
        raise RuntimeError("GROQ_API_KEY no configurada")

    messages = _build_agent_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    groq_tools = _claude_tools_to_openai(select_tools(pregunta))
    total_input = total_output = 0
    msg = None

    for _ in range(MAX_AGENT_ITERATIONS):
        try:
            response = _groq.chat.completions.create(
                model=model,
                max_tokens=4096,
                tools=groq_tools,
                tool_choice="auto",
                messages=messages,
            )
        except GroqBadRequestError as e:
            # Llama 3.3 70B genera tool_calls como XML strings ~10-20% del tiempo
            if "tool_use_failed" in str(e):
                usage = {
                    "input_tokens":      total_input,
                    "output_tokens":     total_output,
                    "cache_read_tokens": 0,
                    "model":             model,
                }
                return _TOOL_USE_FAILED_MSG, usage
            raise
        except GroqRateLimitError:
            usage = {
                "input_tokens":      total_input,
                "output_tokens":     total_output,
                "cache_read_tokens": 0,
                "model":             model,
            }
            return _RATE_LIMIT_MSG, usage

        if response.usage:
            total_input  += response.usage.prompt_tokens or 0
            total_output += response.usage.completion_tokens or 0
        msg = response.choices[0].message
        finish = response.choices[0].finish_reason

        if finish == "stop":
            break

        if finish == "tool_calls" and msg.tool_calls:
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
                try:
                    inputs = _json.loads(tc.function.arguments or "{}")
                except _json.JSONDecodeError:
                    inputs = {}
                result = await execute_tool(tc.function.name, inputs)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result),
                })
        else:
            break

    usage = {
        "input_tokens":      total_input,
        "output_tokens":     total_output,
        "cache_read_tokens": 0,
        "model":             model,
    }
    return (msg.content if msg else "Agente Groq alcanzó límite de iteraciones."), usage


async def stream_agent_groq(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = LLAMA_70B,
    system_prompt: str | None = None,
):
    """Stream agentic con Groq. Mismo formato de chunks que stream_agent_openai
    (chunks de texto + último chunk con \x03JSON usage)."""
    from tools_service import select_tools, execute_tool, begin_tool_events, drain_tool_events
    if _groq is None:
        yield "Error: GROQ_API_KEY no configurada."
        yield f"\x03{_json.dumps({'model': model, 'input_tokens':0,'output_tokens':0,'cache_read_tokens':0})}"
        return

    messages = _build_agent_messages(
        pregunta, doc_context, memory_context,
        business_context, learnings_context, topic_memory,
    )
    groq_tools = _claude_tools_to_openai(select_tools(pregunta))
    total_input = total_output = 0
    begin_tool_events()

    for _ in range(MAX_AGENT_ITERATIONS):
        # Groq soporta streaming, pero el SDK no expone tool_calls bien con stream;
        # usamos no-stream y emitimos el texto al final de cada iteración. UX OK
        # porque Groq es suficientemente rápido (394 TPS).
        try:
            response = _groq.chat.completions.create(
                model=model,
                max_tokens=4096,
                tools=groq_tools,
                tool_choice="auto",
                messages=messages,
            )
        except GroqBadRequestError as e:
            if "tool_use_failed" in str(e):
                yield _TOOL_USE_FAILED_MSG
                usage = {
                    "input_tokens":      total_input,
                    "output_tokens":     total_output,
                    "cache_read_tokens": 0,
                    "model":             model,
                }
                yield f"\x03{_json.dumps(usage)}"
                return
            raise
        except GroqRateLimitError:
            yield _RATE_LIMIT_MSG
            usage = {
                "input_tokens":      total_input,
                "output_tokens":     total_output,
                "cache_read_tokens": 0,
                "model":             model,
            }
            yield f"\x03{_json.dumps(usage)}"
            return

        if response.usage:
            total_input  += response.usage.prompt_tokens or 0
            total_output += response.usage.completion_tokens or 0
        msg    = response.choices[0].message
        finish = response.choices[0].finish_reason

        # FALLBACK: Llama a veces emite tool calls como texto plano
        # (`<function name>{json}</function>`) en lugar de structured tool_calls.
        # Si vemos eso, ejecutamos las tools manualmente y continuamos el loop.
        inline_calls = _parse_inline_function_calls(msg.content or "")
        if inline_calls:
            visible = _strip_inline_function_calls(msg.content or "")
            if visible:
                yield visible
            # Conserva el contenido original (con tags) en el historial para
            # que el modelo sepa qué llamó.
            messages.append({"role": "assistant", "content": msg.content or ""})
            for fn_name, fn_args in inline_calls:
                try:
                    inputs = _json.loads(fn_args or "{}")
                except _json.JSONDecodeError:
                    inputs = {}
                yield f"\n_🔧 Ejecutando: {fn_name}… (inline fallback)_\n"
                result = await execute_tool(fn_name, inputs)
                for ev in drain_tool_events():
                    yield f"\x05{_json.dumps(ev)}\n"
                # Sin tool_call_id, devolvemos el resultado como mensaje de
                # usuario — el modelo lo lee y produce respuesta final.
                messages.append({
                    "role":    "user",
                    "content": f"[Resultado de {fn_name}]\n{result}",
                })
            continue  # nueva iteración para que el modelo cierre la respuesta

        if finish == "stop":
            if msg.content:
                yield msg.content
            break

        if finish == "tool_calls" and msg.tool_calls:
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
                try:
                    inputs = _json.loads(tc.function.arguments or "{}")
                except _json.JSONDecodeError:
                    inputs = {}
                yield f"\n_🔧 Ejecutando: {tc.function.name}…_\n"
                result = await execute_tool(tc.function.name, inputs)
                for ev in drain_tool_events():
                    yield f"\x05{_json.dumps(ev)}\n"
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result),
                })
        else:
            if msg.content:
                yield msg.content
            break

    usage = {
        "input_tokens":      total_input,
        "output_tokens":     total_output,
        "cache_read_tokens": 0,
        "model":             model,
    }
    yield f"\x03{_json.dumps(usage)}"


# ── Non-agente: chat completion sin tools (tier `rapido`) ─────────────────────
# Mismo prompt pipeline que chat_openai pero por Groq. 3x más rápido que GPT-4o
# y ~76% más barato. Sin caching. Calidad ligeramente inferior a GPT-4o en
# tareas largas pero superior a GPT-4o-mini en razonamiento.

_GROQ_MAX_OUTPUT = 4096  # Llama 3.3 70B Groq soporta hasta 8k, dejamos margen


def chat_groq(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = LLAMA_70B,
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    if _groq is None:
        return "Error: GROQ_API_KEY no configurada.", {
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": model,
        }
    from openai_brain import _build_messages
    response = _groq.chat.completions.create(
        model=model,
        max_tokens=_GROQ_MAX_OUTPUT,
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory, system_prompt,
        ),
    )
    return response.choices[0].message.content, _usage_from_response(response.usage, model)


async def stream_chat_groq(
    pregunta: str,
    doc_context: str = "",
    memory_context: str = "",
    business_context: str = "",
    learnings_context: str = "",
    topic_memory: str = "",
    model: str = LLAMA_70B,
    system_prompt: str | None = None,
):
    """Stream con Groq, mismo protocolo que stream_chat_openai: yields chunks de
    texto y termina con \x03JSON usage."""
    if _groq is None:
        yield "Error: GROQ_API_KEY no configurada."
        yield f"\x03{_json.dumps({'input_tokens':0,'output_tokens':0,'cache_read_tokens':0,'model':model})}"
        return
    from openai_brain import _build_messages
    stream = _groq.chat.completions.create(
        model=model,
        max_tokens=_GROQ_MAX_OUTPUT,
        stream=True,
        messages=_build_messages(
            pregunta, doc_context, memory_context,
            business_context, learnings_context, topic_memory, system_prompt,
        ),
    )
    in_tok = out_tok = 0
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta
        usage_obj = getattr(chunk, "usage", None)
        if usage_obj:
            in_tok = getattr(usage_obj, "prompt_tokens", 0) or 0
            out_tok = getattr(usage_obj, "completion_tokens", 0) or 0
    yield f"\x03{_json.dumps({'input_tokens':in_tok,'output_tokens':out_tok,'cache_read_tokens':0,'model':model})}"
