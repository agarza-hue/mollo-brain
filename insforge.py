"""
InsForge — middleware multi-tenant y orquestador de SinergyOS.

Responsabilidades:
1. Autenticación: valida X-API-Key contra sinergy_tenants
2. Autorización: verifica límites de plan y estado de cuenta
3. Orquestación: clasifica complejidad y decide modelo óptimo directamente
   (sin pasar por el pipeline de Mollo — contexto de Adolfo no se inyecta)
4. Contabilidad: incrementa req_used y dispara alertas de uso
"""
import json as _json
import os
import re
import secrets
from typing import AsyncGenerator, Optional

from fastapi import Header, HTTPException, status
from sqlalchemy import text

from db import SessionLocal

# Credenciales directas (independientes de los servicios de Mollo)
_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_OPENAI_KEY    = os.getenv("OPENAI_API_KEY", "")

_CLAUDE_MODEL  = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
_GPT_MINI      = "gpt-4o-mini"
_GPT_4O        = "gpt-4o"

# System prompt base cuando el tenant no define el suyo
_DEFAULT_SYSTEM = (
    "Eres un asistente de inteligencia artificial especializado en estrategia "
    "de negocios y análisis organizacional. Responde en el idioma del usuario. "
    "Usa markdown para mayor claridad. No menciones Claude ni Anthropic."
)

PLAN_LIMITS = {
    "basic":      500,
    "pro":        5_000,
    "enterprise": 999_999,
}


# ── Auth dependency ───────────────────────────────────────────────────────────

def get_tenant(x_api_key: Optional[str] = Header(default=None)) -> Optional[dict]:
    """
    FastAPI dependency — opcional.
    Sin header → None (endpoint usa auth interna de Mollo).
    Header inválido → 401. Cuenta suspendida/pending → 402. Límite → 429.
    OK → dict del tenant.
    """
    if not x_api_key:
        return None

    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT id, slug, name, plan, req_used, req_limit,
                       is_admin, status, system_prompt
                FROM sinergy_tenants
                WHERE api_key = :key
            """),
            {"key": x_api_key},
        ).fetchone()
    finally:
        db.close()

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="API key inválida")

    tenant = dict(row._mapping)

    if tenant.get("status") == "suspended":
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail="Cuenta suspendida. Verifica tu suscripción.")
    if tenant.get("status") == "pending":
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail="Cuenta pendiente de pago. Completa el checkout.")

    if not tenant.get("is_admin"):
        if tenant["req_limit"] < 999_999 and tenant["req_used"] >= tenant["req_limit"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Límite del plan alcanzado ({tenant['req_limit']:,} req/mes). "
                       f"Usados: {tenant['req_used']:,}.",
            )

    return tenant


# ── Routing inteligente ───────────────────────────────────────────────────────

# Indicadores de alta complejidad — requieren Claude
_COMPLEX_PATTERNS = re.compile(
    r"\b(analiza|análisis|estrateg|diseña|propuesta|informe|compara|evalúa|evalua|"
    r"plan estratégico|plan de negocio|diagnóstico|diagnóstico|restructura|"
    r"reorganiza|benchmark|due diligence|valuation|valuación|m&a|fusión|"
    r"adquisición|reestructura|ventaja competitiv|five forces|blue ocean|"
    r"mckinsey|bcg matrix|pestel|canvas)\b",
    re.IGNORECASE,
)
# Indicadores de complejidad media — GPT-4o
_MEDIUM_PATTERNS = re.compile(
    r"\b(cómo|como|explica|explícame|diferencia|comparar|pros y contras|"
    r"ventajas|desventajas|okr|kpi|foda|swot|cuál es la mejor|recomienda|"
    r"sugiéreme|estrategia|implementar|proceso)\b",
    re.IGNORECASE,
)


def _classify(pregunta: str) -> str:
    """Clasifica complejidad: simple | medio | complejo."""
    if len(pregunta) > 300 or _COMPLEX_PATTERNS.search(pregunta):
        return "complejo"
    if len(pregunta) > 80 or _MEDIUM_PATTERNS.search(pregunta):
        return "medio"
    return "simple"


_MODELO_LABEL = {
    "simple":   "GPT-4o-mini",
    "medio":    "GPT-4o",
    "complejo": f"Claude Sonnet 4.6",
}


# ── Streaming directo — Claude ────────────────────────────────────────────────

async def _stream_claude(system: str, pregunta: str, doc_context: str) -> AsyncGenerator[str, None]:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=_ANTHROPIC_KEY)
    user_content = pregunta
    if doc_context:
        user_content = f"DOCUMENTOS RELEVANTES:\n{doc_context}\n\nPREGUNTA: {pregunta}"

    async with client.messages.stream(
        model=_CLAUDE_MODEL,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        async for text in stream.text_stream:
            yield text
        final = await stream.get_final_message()
        usage = {
            "input_tokens":      final.usage.input_tokens,
            "output_tokens":     final.usage.output_tokens,
            "cache_read_tokens": getattr(final.usage, "cache_read_input_tokens", 0),
            "model": _CLAUDE_MODEL,
        }
        yield f"\x03{_json.dumps(usage)}"


# ── Streaming directo — OpenAI ────────────────────────────────────────────────

async def _stream_openai(system: str, pregunta: str, doc_context: str, model: str) -> AsyncGenerator[str, None]:
    from openai import OpenAI
    client = OpenAI(api_key=_OPENAI_KEY)
    user_content = pregunta
    if doc_context:
        user_content = f"DOCUMENTOS RELEVANTES:\n{doc_context}\n\nPREGUNTA: {pregunta}"

    max_tok = 1024 if model == _GPT_MINI else 3000
    stream = client.chat.completions.create(
        model=model,
        max_tokens=max_tok,
        stream=True,
        stream_options={"include_usage": True},
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_content},
        ],
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


# ── Orquestador principal ─────────────────────────────────────────────────────

async def orchestrate_tenant(
    tenant: dict,
    pregunta: str,
    doc_context: str = "",
    modo_override: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Pipeline exclusivo para tenants externos.
    - Sin contexto de Mollo (memoria, empresa, temas de Adolfo)
    - System prompt del tenant (o default SinergyOS)
    - Routing inteligente: simple → GPT-4o-mini, medio → GPT-4o, complejo → Claude
    - Emite header \x02{modo}:{modelo}\n seguido de chunks de texto
    """
    system = tenant.get("system_prompt") or _DEFAULT_SYSTEM
    modo   = modo_override or _classify(pregunta)
    label  = _MODELO_LABEL.get(modo, modo)

    yield f"\x02{modo}:{label}\n"

    if modo == "complejo":
        async for chunk in _stream_claude(system, pregunta, doc_context):
            yield chunk
    elif modo == "medio":
        async for chunk in _stream_openai(system, pregunta, doc_context, _GPT_4O):
            yield chunk
    else:
        async for chunk in _stream_openai(system, pregunta, doc_context, _GPT_MINI):
            yield chunk


# ── Contabilidad ──────────────────────────────────────────────────────────────

def increment_usage(tenant_id: int) -> None:
    """Incrementa req_used en 1 y dispara alertas de uso en umbrales 80% y 100%."""
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE sinergy_tenants SET req_used = req_used + 1 WHERE id = :id"),
            {"id": tenant_id},
        )
        db.commit()

        row = db.execute(
            text("""
                SELECT slug, name, plan, email, req_used, req_limit,
                       alert_sent_80, is_admin
                FROM sinergy_tenants WHERE id = :id
            """),
            {"id": tenant_id},
        ).fetchone()
        if not row:
            return

        t = dict(row._mapping)
        if t["is_admin"] or not t["email"] or t["req_limit"] >= 999_999:
            return

        used, limit = t["req_used"], t["req_limit"]
        pct = used / limit

        if pct >= 0.80 and not t["alert_sent_80"]:
            from alert_service import send_usage_alert_80, send_limit_reached
            if pct >= 1.0:
                send_limit_reached(t["name"], t["email"], t["plan"], limit, t["slug"])
            else:
                send_usage_alert_80(t["name"], t["email"], t["plan"], used, limit, t["slug"])
            db.execute(
                text("UPDATE sinergy_tenants SET alert_sent_80 = TRUE WHERE id = :id"),
                {"id": tenant_id},
            )
            db.commit()
    finally:
        db.close()


# ── Utilidades ────────────────────────────────────────────────────────────────

def generate_api_key() -> str:
    return f"sk-sy-{secrets.token_urlsafe(32)}"
