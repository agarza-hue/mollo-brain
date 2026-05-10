"""Costs router — resumen de tokens y ahorro vs Claude baseline."""
from fastapi import APIRouter
import cost_service

router = APIRouter(prefix="/costs", tags=["Costs"])


@router.get("/summary")
def summary(exclude_modos: str | None = None):
    return {
        "lifetime":    cost_service.lifetime_totals(exclude_modos=exclude_modos),
        "by_model":    cost_service.by_model(exclude_modos=exclude_modos),
        "last_7_days": cost_service.daily_summary(7, exclude_modos=exclude_modos),
    }


@router.get("/lifetime")
def lifetime(exclude_modos: str | None = None):
    return cost_service.lifetime_totals(exclude_modos=exclude_modos)


@router.get("/daily")
def daily(days: int = 30, exclude_modos: str | None = None):
    return cost_service.daily_summary(days, exclude_modos=exclude_modos)


@router.get("/by_model")
def by_model(exclude_modos: str | None = None):
    return cost_service.by_model(exclude_modos=exclude_modos)


@router.get("/topology/interpret")
def topology_interpret(exclude_modos: str | None = None):
    """Lectura del estado de la topología — analiza routing, savings, distribución
    por proveedor y devuelve recomendaciones rule-based. Mismo shape que
    /graph/data interpretation para consistencia UI."""
    lifetime = cost_service.lifetime_totals(exclude_modos=exclude_modos)
    bm = cost_service.by_model(exclude_modos=exclude_modos)
    if not bm:
        return {
            "summary": "Topología vacía. Empieza a usar Mollo para que aparezcan datos.",
            "stats": {}, "hubs": [], "recommendations": [],
        }

    total_q     = lifetime.get("queries", 0) or 0
    total_in    = lifetime.get("input_tokens") or 0
    total_out   = lifetime.get("output_tokens") or 0
    total_cache = lifetime.get("cache_tokens") or 0
    total_cost  = lifetime.get("actual_cost") or 0.0
    baseline    = lifetime.get("baseline_cost") or 0.0
    savings     = lifetime.get("savings") or 0.0
    savings_pct = lifetime.get("savings_pct") or 0
    avg_cost    = (total_cost / total_q) if total_q else 0

    # Cache hit rate (fracción del input que fue cacheado)
    full_input = total_in + total_cache
    cache_hit_pct = (total_cache / full_input * 100) if full_input else 0

    # Distribución por proveedor (basado en model name)
    provider_q: dict = {"OpenAI": 0, "Anthropic": 0, "Google": 0, "Groq": 0, "Otros": 0}
    provider_cost: dict = {"OpenAI": 0.0, "Anthropic": 0.0, "Google": 0.0, "Groq": 0.0, "Otros": 0.0}
    for m in bm:
        model = (m.get("model") or "").lower()
        q = m.get("queries", 0) or 0
        c = m.get("actual_cost", 0) or 0.0
        if "gpt" in model or "openai" in model:
            provider_q["OpenAI"] += q;  provider_cost["OpenAI"] += c
        elif "claude" in model or "anthropic" in model or "haiku" in model or "opus" in model or "sonnet" in model:
            provider_q["Anthropic"] += q;  provider_cost["Anthropic"] += c
        elif "gemini" in model:
            provider_q["Google"] += q;  provider_cost["Google"] += c
        elif "llama" in model:
            provider_q["Groq"] += q;  provider_cost["Groq"] += c
        else:
            provider_q["Otros"] += q;  provider_cost["Otros"] += c

    # Filter providers con queries > 0
    provider_q     = {k: v for k, v in provider_q.items() if v > 0}
    provider_cost  = {k: v for k, v in provider_cost.items() if v > 0}

    dominant_provider = max(provider_q.items(), key=lambda x: x[1]) if provider_q else ("—", 0)
    dom_pct = (dominant_provider[1] / total_q * 100) if total_q else 0

    # ── Hubs: top 3 model+modo por queries ──
    sorted_bm = sorted(bm, key=lambda m: -(m.get("queries", 0) or 0))[:3]
    hubs = []
    for m in sorted_bm:
        sav = m.get("savings", 0) or 0.0
        hubs.append({
            "label":     f"{m.get('model','?')}  ·  {m.get('modo','?')}",
            "categoria": m.get("modo", "—"),
            "degree":    m.get("queries", 0) or 0,
            "extra":     f"${(m.get('actual_cost') or 0):.4f} · ahorro ${sav:.4f}",
        })

    # ── Recomendaciones rule-based ──
    recs = []
    if total_q < 50:
        recs.append({
            "type": "info",
            "msg":  f"Solo {total_q} queries en lifetime — datos limitados. Las recomendaciones serán más precisas con >100 queries."
        })

    if savings_pct < 30 and total_q > 50:
        recs.append({
            "type": "warning",
            "msg":  f"Ahorro vs baseline solo {savings_pct}% — bajo. Revisa que `simple/medio` estén capturando queries que hoy van a `complejo` (Sonnet)."
        })
    elif savings_pct >= 60:
        recs.append({
            "type": "insight",
            "msg":  f"Ahorro {savings_pct}% — el routing está exprimiendo la infra. Cada query baja {savings_pct}% en promedio vs todo-Sonnet."
        })

    if dom_pct > 70 and total_q > 30:
        recs.append({
            "type": "warning",
            "msg":  f"`{dominant_provider[0]}` concentra el {dom_pct:.0f}% de las queries. Single-vendor risk: si ese provider rate-limita, pierdes mayoría del flujo. Considera distribuir más."
        })

    if cache_hit_pct < 20 and total_q > 30:
        recs.append({
            "type": "tip",
            "msg":  f"Cache hit rate {cache_hit_pct:.0f}% — bajo. Verifica que los prefijos del system prompt estén estables (≥1024 tok) para que OpenAI/Anthropic los caché."
        })
    elif cache_hit_pct >= 50:
        recs.append({
            "type": "insight",
            "msg":  f"Cache hit rate {cache_hit_pct:.0f}% — excelente. La mitad del input se procesa a 50-90% off. Mantener los system prompts estables."
        })

    if avg_cost > 0.05:
        recs.append({
            "type": "warning",
            "msg":  f"Costo promedio por query ${avg_cost:.4f} — alto. Para escalar a clientes pagados, este número debe estar más cerca de $0.005-0.015."
        })

    if hubs:
        top = hubs[0]
        recs.append({
            "type": "insight",
            "msg":  f"Hub principal: **{top['label']}** con {top['degree']} queries. Es el caballito de batalla del routing actual."
        })

    # ── Summary natural ──
    summary = (
        f"{total_q} queries lifetime. "
        f"Pagaste **${total_cost:.4f}**, baseline (todo-Sonnet) hubiera sido ${baseline:.4f} → "
        f"ahorraste **{savings_pct}% (${savings:.4f})**. "
        f"Provider dominante: {dominant_provider[0]} ({dom_pct:.0f}%). "
        f"Cache hit {cache_hit_pct:.0f}%."
    )

    return {
        "summary":     summary,
        "stats": {
            "total_queries":     total_q,
            "total_input":       total_in,
            "total_output":      total_out,
            "total_cache":       total_cache,
            "total_cost_usd":    round(total_cost, 4),
            "baseline_usd":      round(baseline, 4),
            "savings_usd":       round(savings, 4),
            "savings_pct":       savings_pct,
            "avg_cost_per_query": round(avg_cost, 5),
            "cache_hit_pct":     round(cache_hit_pct, 1),
            "dominant_provider": dominant_provider[0],
            "dominant_pct":      round(dom_pct, 1),
            "providers_q":       provider_q,
            "providers_cost":    {k: round(v, 4) for k, v in provider_cost.items()},
        },
        "hubs":            hubs,
        "recommendations": recs,
    }


@router.get("/recent")
def recent(limit: int = 20):
    return cost_service.recent(limit)


@router.get("/by_topic")
def by_topic():
    return cost_service.by_topic()


@router.get("/by_provider")
def by_provider():
    return cost_service.by_provider()


@router.get("/topic_by_model")
def topic_by_model():
    return cost_service.topic_by_model()


@router.get("/by_tenant")
def by_tenant():
    return cost_service.by_tenant()


@router.get("/by_tenant_model")
def by_tenant_model():
    return cost_service.by_tenant_model()


@router.get("/top_queries")
def top_queries(limit: int = 5, days: int | None = None):
    return cost_service.top_queries(limit=limit, days=days)


@router.get("/weekly")
def weekly():
    return cost_service.weekly_comparison()


@router.get("/range")
def range_summary(start: str, end: str):
    return cost_service.range_summary(start, end)


@router.get("/roi")
def infrastructure_roi(days: int | None = None):
    return cost_service.infrastructure_roi(days=days)
