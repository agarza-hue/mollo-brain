"""
Mollo Cost Service — tracking de tokens reales y ahorro vs Claude Sonnet baseline.
Persiste en SQLite. Captura tokens reales de OpenAI y Anthropic APIs.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

COST_DB = Path.home() / ".mollo" / "costs.db"
COST_DB.parent.mkdir(exist_ok=True)

# Precios USD por token (mayo 2026)
PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {
        "input":  0.150 / 1_000_000,
        "output": 0.600 / 1_000_000,
        "cache_read": 0.075 / 1_000_000,
    },
    "gpt-4o": {
        "input":  2.50 / 1_000_000,
        "output": 10.00 / 1_000_000,
        "cache_read": 1.25 / 1_000_000,
    },
    "claude-sonnet-4-6": {
        "input":      3.00 / 1_000_000,
        "output":    15.00 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
    },
    "claude-haiku-4-5": {
        "input":      0.80 / 1_000_000,
        "output":     4.00 / 1_000_000,
        "cache_read": 0.08 / 1_000_000,
    },
    "claude-opus-4-7": {
        "input":     15.00 / 1_000_000,
        "output":    75.00 / 1_000_000,
        "cache_read": 1.50 / 1_000_000,
    },
    "llama-3.1-8b-instant": {
        "input":      0.05 / 1_000_000,
        "output":     0.08 / 1_000_000,
        "cache_read": 0.05 / 1_000_000,  # Groq no tiene caching, asumir full price
    },
    "llama-3.3-70b-versatile": {
        "input":      0.59 / 1_000_000,
        "output":     0.79 / 1_000_000,
        "cache_read": 0.59 / 1_000_000,  # Groq sin caching → cobra full price
    },
    "gemini-2.5-flash-lite": {
        "input":      0.10 / 1_000_000,
        "output":     0.40 / 1_000_000,
        "cache_read": 0.01 / 1_000_000,
    },
    "gemini-2.5-pro": {
        # ≤200K tokens. Para >200K (>200K paga $2.50/$15) — raro en este flow.
        "input":      1.25 / 1_000_000,
        "output":    10.00 / 1_000_000,
        "cache_read": 0.125 / 1_000_000,
    },
}

BASELINE_MODEL = "claude-sonnet-4-6"

# Alias para normalizar nombres de modelo
MODEL_ALIASES: dict[str, str] = {
    "gpt-4o-mini":            "gpt-4o-mini",
    "gpt-4.1-mini":           "gpt-4o-mini",
    "gpt-4o":                 "gpt-4o",
    "gpt-4.1":                "gpt-4o",
    "claude-sonnet-4-6":      "claude-sonnet-4-6",
    "claude-sonnet-4-5":      "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-6",
    "claude-sonnet-4-20250514":   "claude-sonnet-4-6",
    "claude-haiku-4-5":       "claude-haiku-4-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-opus-4-7":        "claude-opus-4-7",
    "claude-opus-4-6":        "claude-opus-4-7",
    "claude-opus-4-5-20251101": "claude-opus-4-7",
    "claude-opus-4-1-20250805": "claude-opus-4-7",
    "llama-3.1-8b-instant":   "llama-3.1-8b-instant",
    "llama3-8b-8192":         "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile": "llama-3.3-70b-versatile",
    "llama3-70b-8192":         "llama-3.3-70b-versatile",
    "gemini-2.5-flash-lite":  "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite":  "gemini-2.5-flash-lite",
    "gemini-flash-lite":      "gemini-2.5-flash-lite",
    "gemini-2.5-pro":         "gemini-2.5-pro",
    "gemini-pro":             "gemini-2.5-pro",
}


def _normalize(model: str) -> str:
    return MODEL_ALIASES.get(model, model)


def compute_cost(model: str, input_tokens: int, output_tokens: int,
                 cache_read_tokens: int = 0) -> float:
    p = PRICES.get(_normalize(model), PRICES["gpt-4o"])
    return (
        input_tokens       * p["input"] +
        output_tokens      * p["output"] +
        cache_read_tokens  * p.get("cache_read", 0)
    )


def compute_baseline(input_tokens: int, output_tokens: int,
                     cache_read_tokens: int = 0) -> float:
    return compute_cost(BASELINE_MODEL, input_tokens, output_tokens, cache_read_tokens)


# ── SQLite ─────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(COST_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    with _get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS cost_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts               TEXT    NOT NULL,
            model            TEXT    NOT NULL,
            modo             TEXT    NOT NULL,
            input_tokens     INTEGER NOT NULL DEFAULT 0,
            output_tokens    INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            actual_cost      REAL    NOT NULL DEFAULT 0,
            baseline_cost    REAL    NOT NULL DEFAULT 0,
            savings          REAL    NOT NULL DEFAULT 0,
            query_preview    TEXT    DEFAULT '',
            topic            TEXT    DEFAULT 'general',
            tenant_slug      TEXT    DEFAULT NULL
        )""")
        # Migrations: add columns to existing DBs
        for col, defval in [("topic", "'general'"), ("tenant_slug", "NULL")]:
            try:
                conn.execute(f"ALTER TABLE cost_log ADD COLUMN {col} TEXT DEFAULT {defval}")
            except Exception:
                pass
        conn.commit()


_init()


@contextmanager
def _db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Public API ─────────────────────────────────────────────────────────────

def record(
    model: str,
    modo: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    query_preview: str = "",
    topic: str = "general",
    tenant_slug: Optional[str] = None,
):
    """Registra una query y sus tokens reales."""
    norm = _normalize(model)
    actual   = compute_cost(norm, input_tokens, output_tokens, cache_read_tokens)
    baseline = compute_baseline(input_tokens, output_tokens, cache_read_tokens)
    savings  = baseline - actual

    with _db() as conn:
        conn.execute(
            """INSERT INTO cost_log
               (ts, model, modo, input_tokens, output_tokens, cache_read_tokens,
                actual_cost, baseline_cost, savings, query_preview, topic, tenant_slug)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                norm, modo, input_tokens, output_tokens, cache_read_tokens,
                actual, baseline, savings, query_preview[:80], topic, tenant_slug,
            ),
        )


def _exclude_clause(exclude_modos: str | None, prefix: str = "AND") -> tuple[str, list]:
    """Construye un fragmento SQL `AND modo NOT IN (?,?,..)` parametrizado.
    `exclude_modos` es CSV ('claude_code,probe' o vacío). Retorna ('', []) si
    no hay nada que excluir, listo para concatenar."""
    if not exclude_modos:
        return "", []
    modos = [m.strip() for m in exclude_modos.split(",") if m.strip()]
    if not modos:
        return "", []
    placeholders = ",".join(["?"] * len(modos))
    return f" {prefix} modo NOT IN ({placeholders})", modos


def lifetime_totals(exclude_modos: str | None = None) -> dict:
    where, params = _exclude_clause(exclude_modos, prefix="WHERE")
    sql = f"""
        SELECT
            COUNT(*)         AS queries,
            SUM(input_tokens)      AS input_tokens,
            SUM(output_tokens)     AS output_tokens,
            SUM(cache_read_tokens) AS cache_tokens,
            SUM(actual_cost)       AS actual_cost,
            SUM(baseline_cost)     AS baseline_cost,
            SUM(savings)           AS savings
        FROM cost_log
        {where}
    """
    with _db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    d = dict(row)
    d["savings_pct"] = round(d["savings"] / d["baseline_cost"] * 100, 1) if d["baseline_cost"] else 0
    return d


def daily_summary(days: int = 7, exclude_modos: str | None = None) -> list[dict]:
    extra, params = _exclude_clause(exclude_modos)
    sql = f"""
        SELECT
            DATE(ts) AS day,
            COUNT(*) AS queries,
            SUM(input_tokens + output_tokens) AS total_tokens,
            SUM(actual_cost)   AS actual_cost,
            SUM(baseline_cost) AS baseline_cost,
            SUM(savings)       AS savings
        FROM cost_log
        WHERE ts >= DATE('now', '-{int(days)} days')
        {extra}
        GROUP BY day
        ORDER BY day DESC
    """
    with _db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def by_model(exclude_modos: str | None = None) -> list[dict]:
    where, params = _exclude_clause(exclude_modos, prefix="WHERE")
    sql = f"""
        SELECT
            model,
            modo,
            COUNT(*) AS queries,
            SUM(input_tokens)      AS input_tokens,
            SUM(output_tokens)     AS output_tokens,
            SUM(cache_read_tokens) AS cache_tokens,
            SUM(actual_cost)       AS actual_cost,
            SUM(baseline_cost)     AS baseline_cost,
            SUM(savings)           AS savings
        FROM cost_log
        {where}
        GROUP BY model, modo
        ORDER BY queries DESC
    """
    with _db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def recent(limit: int = 20) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM cost_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def range_summary(start_date: str, end_date: str) -> dict:
    """Totales de cost_log entre dos fechas (inclusive). Excluye probe.
    Devuelve breakdown por modo + lifetime sums."""
    sql = """
        SELECT modo,
               COUNT(*) AS queries,
               COALESCE(SUM(input_tokens),0)      AS input_tokens,
               COALESCE(SUM(output_tokens),0)     AS output_tokens,
               COALESCE(SUM(cache_read_tokens),0) AS cache_tokens,
               COALESCE(SUM(actual_cost),0)       AS actual_cost
        FROM cost_log
        WHERE date(ts) BETWEEN ? AND ?
          AND modo != 'probe'
        GROUP BY modo
    """
    with _db() as conn:
        rows = [dict(r) for r in conn.execute(sql, (start_date, end_date))]
    totals = {
        'queries':      sum(r['queries']       for r in rows),
        'input_tokens': sum(r['input_tokens']  for r in rows),
        'output_tokens':sum(r['output_tokens'] for r in rows),
        'cache_tokens': sum(r['cache_tokens']  for r in rows),
        'actual_cost':  sum(r['actual_cost']   for r in rows),
    }
    return {'start': start_date, 'end': end_date, 'by_modo': rows, 'totals': totals}


def infrastructure_roi(days: int | None = None) -> dict:
    """ROI de la infraestructura — qué tanto ahorra mollo_brain vs el
    contraescenario "todo via Claude.ai Max sin routing". Computado en 2
    capas independientes:

      A) ROUTING GAP — sólo modos del routing interno (simple, medio,
         agente, complejo, external). Compara costo real contra costo si
         TODO se hubiera hecho en Sonnet 4.6 (baseline natural pre-mollo).

      B) OPUS CACHE OPTIMIZATION — sólo modo='claude_code'. Compara costo
         real (con prompt caching activo, $1.50/1M cache_read) contra
         costo si los cache_read_tokens hubieran sido input regulares
         ($15/1M) — i.e., sin prompt caching habilitado.

      C) HOURS FREED — cada query que mollo_brain procesó es una que NO
         consumió tu cuota Max ($100/mo, 225 msgs / ventana 5h en Max 5x).
         Estimamos ventanas de 5h "rescatadas" = total_queries / 225.

    Si `days` se da, el rango se limita a últimos N días. None = lifetime.
    """
    where_time = ""
    params: list = []
    if days is not None:
        where_time = " AND ts >= datetime('now', ?)"
        params.append(f"-{int(days)} days")

    routing_modos = ('simple', 'medio', 'agente', 'complejo', 'external')

    # A) Routing GAP
    placeholders = ",".join(["?"] * len(routing_modos))
    sql = f"""
        SELECT
            COUNT(*) AS queries,
            COALESCE(SUM(actual_cost),0)    AS actual,
            COALESCE(SUM(baseline_cost),0)  AS baseline,
            COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens),0) AS total_tokens
        FROM cost_log
        WHERE modo IN ({placeholders}) {where_time}
    """
    with _db() as conn:
        row = conn.execute(sql, tuple(routing_modos) + tuple(params)).fetchone()
    routing = dict(row)
    routing["gap_usd"] = round(routing["baseline"] - routing["actual"], 4)
    routing["gap_pct"] = round((routing["gap_usd"] / routing["baseline"]) * 100, 1) if routing["baseline"] else 0

    # B) Opus cache optimization (claude_code)
    sql_opus = f"""
        SELECT
            COUNT(*) AS queries,
            COALESCE(SUM(actual_cost),0)        AS actual,
            COALESCE(SUM(input_tokens),0)       AS input_tokens,
            COALESCE(SUM(output_tokens),0)      AS output_tokens,
            COALESCE(SUM(cache_read_tokens),0)  AS cache_tokens
        FROM cost_log
        WHERE modo = 'claude_code' {where_time}
    """
    with _db() as conn:
        row = conn.execute(sql_opus, tuple(params)).fetchone()
    opus = dict(row)
    # Costo si NO hubiera cache: cache_read_tokens tratados como input regular Opus
    opus_p = PRICES.get("claude-opus-4-7", PRICES["claude-sonnet-4-6"])
    if opus["queries"] > 0:
        cost_no_cache = (
            (opus["input_tokens"] + opus["cache_tokens"]) * opus_p["input"]
            + opus["output_tokens"] * opus_p["output"]
        )
        opus["no_cache_cost"] = round(cost_no_cache, 4)
        opus["cache_savings_usd"] = round(cost_no_cache - opus["actual"], 4)
        opus["cache_savings_pct"] = round((opus["cache_savings_usd"] / cost_no_cache) * 100, 1) if cost_no_cache else 0
    else:
        opus["no_cache_cost"] = 0
        opus["cache_savings_usd"] = 0
        opus["cache_savings_pct"] = 0

    # C) Hours freed — SÓLO queries que mollo_brain absorbió vía API key
    # (routing real). claude_code NO califica porque está autenticado vía
    # OAuth/Max y consume del MISMO bucket que claude.ai web — no descarga.
    sql_hours = f"""
        SELECT COUNT(*) AS queries
        FROM cost_log
        WHERE modo NOT IN ('probe', 'external', 'claude_code') {where_time}
    """
    with _db() as conn:
        row = conn.execute(sql_hours, tuple(params)).fetchone()
    n_freed = row["queries"] or 0
    MAX_5X_QUOTA_PER_5H = 225
    hours_freed_estimate = round(n_freed / MAX_5X_QUOTA_PER_5H * 5, 1)  # ventanas × 5h

    # D) Capacidad instalada vs uso real — 3 periodos calendario con reset
    # Max 5x: 225 msgs/ventana 5h × ~5 ventanas/día. Cifras conservadoras
    # (Anthropic publica rangos variables, usamos lower bound).
    WINDOWS_PER_DAY  = 5  # 24/5 = 4.8, redondeado conservador
    DAILY_CAPACITY   = MAX_5X_QUOTA_PER_5H * WINDOWS_PER_DAY  # 1,125
    WEEKLY_CAPACITY  = DAILY_CAPACITY * 7                      # 7,875
    MONTHLY_CAPACITY = DAILY_CAPACITY * 30                     # 33,750

    # Reset cada periodo — alineado con Anthropic Max 5x:
    #   día:    00:00 local (CST/CDT). SQL: date(ts) = date('now', 'localtime')
    #   semana: Jueves 1:00 AM local (Anthropic reset oficial). Calcula
    #           threshold en Python para manejar DST sin sorpresas
    #   mes:    día 1 00:00 local. SQL: ts >= date('now', 'start of month', 'localtime')
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    # CST/CDT: server tz. Usamos datetime "naive local" → asumimos que el
    # server vive en la TZ del user (Mexico/Monterrey). Si el server moviera
    # de TZ habría que cambiar a zoneinfo.
    now_local = _dt.now()
    # Buscar el último jueves cuya hora 1:00 AM ya pasó
    # weekday(): Mon=0...Thu=3...Sun=6
    days_since_thursday = (now_local.weekday() - 3) % 7
    last_thu = now_local - _td(days=days_since_thursday)
    last_thu_reset = last_thu.replace(hour=1, minute=0, second=0, microsecond=0)
    if last_thu_reset > now_local:
        # aún no pasó la 1 AM de este jueves; usar el jueves previo
        last_thu_reset = last_thu_reset - _td(days=7)
    # Convertir a UTC ISO para comparar con ts almacenado
    week_threshold_utc = last_thu_reset.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")

    # Filtro de offload: excluye claude_code porque es OAuth/Max (mismo bucket
    # que claude.ai). Sólo contamos lo que va vía API key (routing real).
    queries_filter = "modo NOT IN ('probe', 'external', 'claude_code')"
    with _db() as conn:
        used_today = conn.execute(
            f"SELECT COUNT(*) AS q FROM cost_log WHERE {queries_filter} AND date(ts, 'localtime')=date('now', 'localtime')"
        ).fetchone()["q"] or 0
        used_week = conn.execute(
            f"SELECT COUNT(*) AS q FROM cost_log WHERE {queries_filter} AND ts >= ?",
            (week_threshold_utc,)
        ).fetchone()["q"] or 0
        used_month = conn.execute(
            f"SELECT COUNT(*) AS q FROM cost_log WHERE {queries_filter} AND date(ts, 'localtime') >= date('now', 'start of month', 'localtime')"
        ).fetchone()["q"] or 0

    def _period(used: int, cap: int, label: str) -> dict:
        return {
            "label":       label,
            "capacity":    cap,
            "used":        used,
            "headroom":    cap - used,
            "utilization_pct": round((used / cap) * 100, 2) if cap else 0,
        }

    return {
        "period_days": days,
        "routing": routing,
        "opus_cache": opus,
        "hours": {
            "queries_processed": n_freed,
            "max_quota_per_5h_window": MAX_5X_QUOTA_PER_5H,
            "windows_freed": round(n_freed / MAX_5X_QUOTA_PER_5H, 2),
            "hours_freed_estimate": hours_freed_estimate,
        },
        "capacity": {
            "plan": "Max 5x",
            "msgs_per_5h_window": MAX_5X_QUOTA_PER_5H,
            "calc_basis": f"{MAX_5X_QUOTA_PER_5H} msgs/5h × {WINDOWS_PER_DAY} vent./día",
            "daily":    _period(used_today, DAILY_CAPACITY,   "Hoy"),
            "weekly":   _period(used_week,  WEEKLY_CAPACITY,  "Esta semana"),
            "monthly":  _period(used_month, MONTHLY_CAPACITY, "Este mes"),
        },
    }


def weekly_comparison() -> dict:
    """Esta semana (últimos 7d) vs anterior (días -8 a -14).
    Excluye modo='external' (logs Claude Code históricos) y 'probe' (health-checks).
    """
    base_sql = """
        SELECT
            COUNT(*) AS queries,
            COALESCE(SUM(input_tokens),0)      AS input_tokens,
            COALESCE(SUM(output_tokens),0)     AS output_tokens,
            COALESCE(SUM(cache_read_tokens),0) AS cache_tokens,
            COALESCE(SUM(actual_cost),0)       AS actual_cost,
            COALESCE(SUM(baseline_cost),0)     AS baseline_cost,
            COALESCE(SUM(savings),0)           AS savings
        FROM cost_log
        WHERE modo NOT IN ('external','probe')
    """
    with _db() as conn:
        this_week = dict(conn.execute(
            base_sql + " AND ts >= datetime('now','-7 days')"
        ).fetchone())
        prior_week = dict(conn.execute(
            base_sql + " AND ts >= datetime('now','-14 days') AND ts < datetime('now','-7 days')"
        ).fetchone())

    def pct(curr, prev):
        if not prev:
            return None
        return ((curr - prev) / prev) * 100

    deltas = {
        k: pct(this_week[k], prior_week[k])
        for k in ('queries','input_tokens','output_tokens','cache_tokens',
                  'actual_cost','baseline_cost','savings')
    }
    return {'this_week': this_week, 'prior_week': prior_week, 'delta_pct': deltas}


def top_queries(limit: int = 5, days: int | None = None) -> list[dict]:
    # Excluye modos no-mollo_brain (external = logs importados de Claude Code
    # directo, probe = pings de health-check de /limits/probe).
    sql = """
        SELECT id, ts, model, modo, input_tokens, output_tokens,
               cache_read_tokens AS cache_tokens,
               actual_cost, baseline_cost, savings,
               query_preview, topic, tenant_slug
        FROM cost_log
        WHERE modo NOT IN ('external', 'probe')
    """
    params: list = []
    if days is not None:
        sql += " AND ts >= datetime('now', ?) "
        params.append(f"-{int(days)} days")
    sql += " ORDER BY actual_cost DESC LIMIT ?"
    params.append(int(limit))
    with _db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def by_topic() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(topic, 'general') AS topic,
                COUNT(*)                   AS queries,
                SUM(input_tokens)          AS input_tokens,
                SUM(output_tokens)         AS output_tokens,
                SUM(cache_read_tokens)     AS cache_tokens,
                SUM(actual_cost)           AS actual_cost,
                SUM(baseline_cost)         AS baseline_cost,
                SUM(savings)               AS savings
            FROM cost_log
            GROUP BY topic
            ORDER BY queries DESC
        """).fetchall()
    return [dict(r) for r in rows]


def by_provider() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("""
            SELECT
                CASE
                    WHEN model LIKE 'gpt%'    THEN 'OpenAI'
                    WHEN model LIKE 'claude%' THEN 'Anthropic'
                    WHEN model LIKE 'gemini%' THEN 'Google'
                    ELSE 'Otro'
                END AS provider,
                COUNT(*)                   AS queries,
                SUM(input_tokens)          AS input_tokens,
                SUM(output_tokens)         AS output_tokens,
                SUM(cache_read_tokens)     AS cache_tokens,
                SUM(actual_cost)           AS actual_cost,
                SUM(baseline_cost)         AS baseline_cost,
                SUM(savings)               AS savings
            FROM cost_log
            GROUP BY provider
            ORDER BY queries DESC
        """).fetchall()
    return [dict(r) for r in rows]


def topic_by_model() -> list[dict]:
    """Matriz topic × model para el heatmap del dashboard."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(topic, 'general') AS topic,
                model,
                COUNT(*)         AS queries,
                SUM(input_tokens + output_tokens) AS total_tokens,
                SUM(actual_cost) AS actual_cost,
                SUM(savings)     AS savings
            FROM cost_log
            GROUP BY topic, model
            ORDER BY topic, queries DESC
        """).fetchall()
    return [dict(r) for r in rows]


def by_tenant() -> list[dict]:
    """Costo total agrupado por tenant_slug (mes actual y lifetime)."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(tenant_slug, '__internal__') AS tenant_slug,
                COUNT(*)                              AS queries,
                SUM(input_tokens)                     AS input_tokens,
                SUM(output_tokens)                    AS output_tokens,
                SUM(cache_read_tokens)                AS cache_tokens,
                SUM(actual_cost)                      AS actual_cost,
                SUM(baseline_cost)                    AS baseline_cost,
                SUM(savings)                          AS savings,
                SUM(CASE WHEN ts >= DATE('now','start of month')
                         THEN actual_cost ELSE 0 END) AS cost_this_month,
                SUM(CASE WHEN ts >= DATE('now','start of month')
                         THEN 1 ELSE 0 END)           AS queries_this_month
            FROM cost_log
            GROUP BY tenant_slug
            ORDER BY actual_cost DESC
        """).fetchall()
    return [dict(r) for r in rows]


def by_tenant_model() -> list[dict]:
    """Desglose por tenant × modelo — para análisis costo-beneficio en admin."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(tenant_slug, '__internal__')       AS tenant_slug,
                CASE
                    WHEN model LIKE 'claude%' THEN 'Claude'
                    WHEN model LIKE 'gpt-4o-mini%' THEN 'GPT-4o-mini'
                    WHEN model LIKE 'gpt-4o%' THEN 'GPT-4o'
                    ELSE model
                END                                         AS provider,
                model,
                COUNT(*)                                    AS queries,
                SUM(input_tokens)                           AS input_tokens,
                SUM(output_tokens)                          AS output_tokens,
                SUM(cache_read_tokens)                      AS cache_tokens,
                SUM(actual_cost)                            AS actual_cost,
                SUM(CASE WHEN ts >= DATE('now','start of month')
                         THEN actual_cost ELSE 0 END)       AS cost_this_month,
                SUM(CASE WHEN ts >= DATE('now','start of month')
                         THEN input_tokens + output_tokens ELSE 0 END) AS tokens_this_month
            FROM cost_log
            WHERE tenant_slug IS NOT NULL AND tenant_slug != '__internal__'
            GROUP BY tenant_slug, model
            ORDER BY tenant_slug, actual_cost DESC
        """).fetchall()
    return [dict(r) for r in rows]


def session_totals(since_ts: str) -> dict:
    with _db() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)         AS queries,
                SUM(input_tokens + output_tokens) AS total_tokens,
                SUM(actual_cost)   AS actual_cost,
                SUM(baseline_cost) AS baseline_cost,
                SUM(savings)       AS savings
            FROM cost_log WHERE ts >= ?
        """, (since_ts,)).fetchone()
    d = dict(row)
    d["savings_pct"] = round(d["savings"] / d["baseline_cost"] * 100, 1) if d["baseline_cost"] else 0
    return d
