"""
Limits router — sondea los rate limits reales de Anthropic y OpenAI,
combina con uso acumulado en SQLite y alertas de presupuesto.
Cachea el probe 60s para no gastar tokens en cada refresh del dashboard.
"""
import time
import asyncio
from datetime import datetime, timezone, date
from fastapi import APIRouter
import httpx
import cost_service
from config import ANTHROPIC_API_KEY, OPENAI_API_KEY

router = APIRouter(prefix="/limits", tags=["Limits"])

# ── Cache ────────────────────────────────────────────────────────────────────
_cache: dict = {}
_cache_ts: float = 0
CACHE_TTL = 60  # segundos


# ── Probe helpers ─────────────────────────────────────────────────────────────

async def _probe_anthropic() -> dict:
    """Llama a Claude Haiku con max_tokens=1, captura headers y registra el costo."""
    headers_out = {}
    error = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "1"}],
                },
            )
            h = r.headers
            headers_out = {
                "input_limit":     int(h.get("anthropic-ratelimit-input-tokens-limit",   0)),
                "input_remaining": int(h.get("anthropic-ratelimit-input-tokens-remaining", 0)),
                "input_reset":     h.get("anthropic-ratelimit-input-tokens-reset", ""),
                "output_limit":    int(h.get("anthropic-ratelimit-output-tokens-limit",  0)),
                "output_remaining":int(h.get("anthropic-ratelimit-output-tokens-remaining", 0)),
                "output_reset":    h.get("anthropic-ratelimit-output-tokens-reset", ""),
                "req_limit":       int(h.get("anthropic-ratelimit-requests-limit",       0)),
                "req_remaining":   int(h.get("anthropic-ratelimit-requests-remaining",   0)),
                "req_reset":       h.get("anthropic-ratelimit-requests-reset", ""),
                "tokens_limit":    int(h.get("anthropic-ratelimit-tokens-limit",         0)),
                "tokens_remaining":int(h.get("anthropic-ratelimit-tokens-remaining",     0)),
                "tokens_reset":    h.get("anthropic-ratelimit-tokens-reset", ""),
                "status_code":     r.status_code,
            }
            # Registrar tokens del probe (no son gratis)
            if r.status_code == 200:
                body = r.json()
                usage = body.get("usage", {})
                in_tok  = usage.get("input_tokens",  0)
                out_tok = usage.get("output_tokens", 0)
                if in_tok or out_tok:
                    cost_service.record(
                        model="claude-haiku-4-5",
                        modo="probe",
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        query_preview="[rate-limit probe]",
                        topic="automatizacion",
                    )
    except Exception as e:
        error = str(e)
    return {"provider": "Anthropic", "headers": headers_out, "error": error}


async def _probe_openai() -> dict:
    """Llama a GPT-4o-mini con max_tokens=1 y captura headers de rate limit."""
    headers_out = {}
    error = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "1"}],
                },
            )
            h = r.headers
            headers_out = {
                "req_limit":       int(h.get("x-ratelimit-limit-requests",     0)),
                "req_remaining":   int(h.get("x-ratelimit-remaining-requests", 0)),
                "req_reset":       h.get("x-ratelimit-reset-requests", ""),
                "tokens_limit":    int(h.get("x-ratelimit-limit-tokens",       0)),
                "tokens_remaining":int(h.get("x-ratelimit-remaining-tokens",   0)),
                "tokens_reset":    h.get("x-ratelimit-reset-tokens", ""),
                "status_code":     r.status_code,
            }
            # Registrar tokens del probe
            if r.status_code == 200:
                body = r.json()
                usage = body.get("usage", {})
                in_tok  = usage.get("prompt_tokens",     0)
                out_tok = usage.get("completion_tokens", 0)
                if in_tok or out_tok:
                    cost_service.record(
                        model="gpt-4o-mini",
                        modo="probe",
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        query_preview="[rate-limit probe]",
                        topic="automatizacion",
                    )
    except Exception as e:
        error = str(e)
    return {"provider": "OpenAI", "headers": headers_out, "error": error}


def _usage_stats() -> dict:
    """Uso acumulado de nuestra SQLite, desglosado por proveedor y mes."""
    import sqlite3
    from pathlib import Path

    db = Path.home() / ".mollo" / "costs.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    # lifetime por proveedor
    by_prov = conn.execute("""
        SELECT
            CASE
                WHEN model LIKE 'gpt%'    THEN 'OpenAI'
                WHEN model LIKE 'claude%' THEN 'Anthropic'
                ELSE 'Otro'
            END AS provider,
            SUM(input_tokens)      AS input_tokens,
            SUM(output_tokens)     AS output_tokens,
            SUM(cache_read_tokens) AS cache_tokens,
            SUM(actual_cost)       AS actual_cost,
            COUNT(*)               AS queries,
            MIN(ts)                AS first_seen
        FROM cost_log
        GROUP BY provider
    """).fetchall()

    # por mes
    by_month = conn.execute("""
        SELECT
            strftime('%Y-%m', ts) AS month,
            CASE
                WHEN model LIKE 'gpt%'    THEN 'OpenAI'
                WHEN model LIKE 'claude%' THEN 'Anthropic'
                ELSE 'Otro'
            END AS provider,
            SUM(input_tokens + output_tokens + cache_read_tokens) AS tokens,
            SUM(actual_cost) AS cost,
            COUNT(*) AS queries
        FROM cost_log
        GROUP BY month, provider
        ORDER BY month DESC
    """).fetchall()

    # hoy
    today = date.today().isoformat()
    today_row = conn.execute("""
        SELECT COUNT(*) AS q, SUM(actual_cost) AS cost
        FROM cost_log WHERE DATE(ts) = ?
    """, (today,)).fetchone()

    conn.close()
    return {
        "by_provider": [dict(r) for r in by_prov],
        "by_month":    [dict(r) for r in by_month],
        "today":       dict(today_row) if today_row else {"q": 0, "cost": 0},
        "first_record": None,  # se llena abajo
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/probe")
async def probe():
    """
    Sondea rate limits en tiempo real de Anthropic y OpenAI.
    Cachea 60s para evitar spam de tokens.
    """
    global _cache, _cache_ts
    now = time.time()
    if now - _cache_ts < CACHE_TTL and _cache:
        return {**_cache, "cached": True, "cache_age_s": int(now - _cache_ts)}

    anthropic_result, openai_result = await asyncio.gather(
        _probe_anthropic(),
        _probe_openai(),
    )

    usage = _usage_stats()

    result = {
        "ts":        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anthropic": anthropic_result,
        "openai":    openai_result,
        "usage":     usage,
        "cached":    False,
        "cache_age_s": 0,
    }
    _cache    = result
    _cache_ts = now
    return result


@router.get("/usage")
def usage_only():
    """Solo el uso acumulado en SQLite, sin probe de APIs (rápido)."""
    return _usage_stats()
