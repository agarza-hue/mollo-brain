"""
Mollo Cost Service — tracking de tokens reales y ahorro vs Claude Sonnet baseline.
Persiste en SQLite. Captura tokens reales de OpenAI y Anthropic APIs.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

COST_DB = Path.home() / ".mollo" / "costs.db"
COST_DB.parent.mkdir(exist_ok=True)

USD_PER_MILLION_TOKENS = 1_000_000
DEFAULT_MODEL = "gpt-4o"
BASELINE_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-7"

PROBE_MODO = "probe"
EXTERNAL_MODO = "external"
CLAUDE_CODE_MODO = "claude_code"

ROUTING_MODOS = ("simple", "medio", "agente", "complejo", "external")
NON_MOLLO_MODOS = (EXTERNAL_MODO, PROBE_MODO)
OFFLOAD_EXCLUDED_MODOS = (PROBE_MODO, EXTERNAL_MODO, CLAUDE_CODE_MODO)

MAX_5X_QUOTA_PER_5H = 225
WINDOWS_PER_DAY = 5  # 24/5 = 4.8, redondeado conservador
DAILY_CAPACITY = MAX_5X_QUOTA_PER_5H * WINDOWS_PER_DAY
WEEKLY_CAPACITY = DAILY_CAPACITY * 7
MONTHLY_CAPACITY = DAILY_CAPACITY * 30
QUERY_PREVIEW_MAX_CHARS = 80
MAX_WINDOW_HOURS = 5
WEEKLY_RESET_WEEKDAY = 3  # weekday(): Mon=0...Thu=3...Sun=6
WEEKLY_RESET_HOUR = 1
DEFAULT_TOPIC = "general"
INTERNAL_TENANT = "__internal__"
MONTH_START_SQL = "DATE('now','start of month')"
LOCAL_TODAY_SQL = "date('now', 'localtime')"
MIGRATION_COLUMNS = (("topic", "'general'"), ("tenant_slug", "NULL"))
COMPARISON_METRICS = (
    "queries",
    "input_tokens",
    "output_tokens",
    "cache_tokens",
    "actual_cost",
    "baseline_cost",
    "savings",
)
PROVIDER_OPENAI = "OpenAI"
PROVIDER_ANTHROPIC = "Anthropic"
PROVIDER_GOOGLE = "Google"
PROVIDER_OTHER = "Otro"

# Precios USD por token (mayo 2026)
PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {
        "input":  0.150 / USD_PER_MILLION_TOKENS,
        "output": 0.600 / USD_PER_MILLION_TOKENS,
        "cache_read": 0.075 / USD_PER_MILLION_TOKENS,
    },
    "gpt-4o": {
        "input":  2.50 / USD_PER_MILLION_TOKENS,
        "output": 10.00 / USD_PER_MILLION_TOKENS,
        "cache_read": 1.25 / USD_PER_MILLION_TOKENS,
    },
    "claude-sonnet-4-6": {
        "input":      3.00 / USD_PER_MILLION_TOKENS,
        "output":    15.00 / USD_PER_MILLION_TOKENS,
        "cache_read": 0.30 / USD_PER_MILLION_TOKENS,
    },
    "claude-haiku-4-5": {
        "input":      0.80 / USD_PER_MILLION_TOKENS,
        "output":     4.00 / USD_PER_MILLION_TOKENS,
        "cache_read": 0.08 / USD_PER_MILLION_TOKENS,
    },
    "claude-opus-4-7": {
        "input":     15.00 / USD_PER_MILLION_TOKENS,
        "output":    75.00 / USD_PER_MILLION_TOKENS,
        "cache_read": 1.50 / USD_PER_MILLION_TOKENS,
    },
    "llama-3.1-8b-instant": {
        "input":      0.05 / USD_PER_MILLION_TOKENS,
        "output":     0.08 / USD_PER_MILLION_TOKENS,
        "cache_read": 0.05 / USD_PER_MILLION_TOKENS,  # Groq no tiene caching, asumir full price
    },
    "llama-3.3-70b-versatile": {
        "input":      0.59 / USD_PER_MILLION_TOKENS,
        "output":     0.79 / USD_PER_MILLION_TOKENS,
        "cache_read": 0.59 / USD_PER_MILLION_TOKENS,  # Groq sin caching → cobra full price
    },
    "gemini-2.5-flash-lite": {
        "input":      0.10 / USD_PER_MILLION_TOKENS,
        "output":     0.40 / USD_PER_MILLION_TOKENS,
        "cache_read": 0.01 / USD_PER_MILLION_TOKENS,
    },
    "gemini-2.5-pro": {
        # ≤200K tokens. Para >200K (>200K paga $2.50/$15) — raro en este flow.
        "input":      1.25 / USD_PER_MILLION_TOKENS,
        "output":    10.00 / USD_PER_MILLION_TOKENS,
        "cache_read": 0.125 / USD_PER_MILLION_TOKENS,
    },
}

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
    """Devuelve el nombre canónico del modelo usado para pricing/reportes."""
    return MODEL_ALIASES.get(model, model)


def compute_cost(model: str, input_tokens: int, output_tokens: int,
                 cache_read_tokens: int = 0) -> float:
    """Calcula costo USD con tarifas por token para el modelo normalizado."""
    p = PRICES.get(_normalize(model), PRICES[DEFAULT_MODEL])
    return (
        input_tokens       * p["input"] +
        output_tokens      * p["output"] +
        cache_read_tokens  * p.get("cache_read", 0)
    )


def compute_baseline(input_tokens: int, output_tokens: int,
                     cache_read_tokens: int = 0) -> float:
    """Costo equivalente si la query hubiera usado el modelo baseline."""
    return compute_cost(BASELINE_MODEL, input_tokens, output_tokens, cache_read_tokens)


# ── SQLite ─────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(COST_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    """Crea la tabla de tracking y aplica migraciones livianas idempotentes."""
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
        for col, defval in MIGRATION_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE cost_log ADD COLUMN {col} TEXT DEFAULT {defval}")
            except Exception:
                pass
        conn.commit()


_init()


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Context manager SQLite con commit automático al salir sin excepción."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _fetch_all(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return _rows_to_dicts(rows)


def _fetch_one_dict(sql: str, params: Sequence[Any] = ()) -> dict[str, Any]:
    with _db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return dict(row)


def _savings_pct(savings: Any, baseline_cost: Any) -> float | int:
    return round(savings / baseline_cost * 100, 1) if baseline_cost else 0


def _period(used: int, cap: int, label: str) -> dict[str, Any]:
    return {
        "label":       label,
        "capacity":    cap,
        "used":        used,
        "headroom":    cap - used,
        "utilization_pct": round((used / cap) * 100, 2) if cap else 0,
    }


def _count_query(sql: str, params: Sequence[Any] = ()) -> int:
    """Ejecuta una consulta que devuelve una columna `q` con conteo."""
    with _db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return row["q"] or 0


def _sum_rows(rows: Sequence[dict[str, Any]], keys: Sequence[str]) -> dict[str, Any]:
    """Suma columnas numéricas de una lista de dicts preservando nombres."""
    return {key: sum(row[key] for row in rows) for key in keys}


def _pct_delta(curr: float | int | None, prev: float | int | None) -> float | None:
    """Cambio porcentual entre dos valores; None cuando no hay base previa."""
    if not prev:
        return None
    return ((curr - prev) / prev) * 100


def _in_clause_values(values: tuple[str, ...]) -> str:
    """Placeholders para cláusulas IN parametrizadas."""
    return ",".join(["?"] * len(values))


def _days_filter(days: int | None) -> tuple[str, list[str]]:
    """Filtro SQL para limitar consultas a los últimos N días."""
    if days is None:
        return "", []
    return " AND ts >= datetime('now', ?)", [f"-{int(days)} days"]


def _roi_time_filter(days: int | None) -> tuple[str, list[str]]:
    """Fragmento SQL y parámetros para filtrar ROI por últimos N días."""
    if days is None:
        return "", []
    return " AND ts >= datetime('now', ?)", [f"-{int(days)} days"]


def _offload_mode_filter() -> str:
    """Filtro SQL parametrizado para queries que sí descargan cuota Max."""
    return f"modo NOT IN ({_in_clause_values(OFFLOAD_EXCLUDED_MODOS)})"


def _last_weekly_reset_utc() -> str:
    """Último reset semanal Max 5x en UTC ISO, usando timezone local del server."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    now_local = _dt.now()
    days_since_reset = (now_local.weekday() - WEEKLY_RESET_WEEKDAY) % 7
    last_reset = now_local - _td(days=days_since_reset)
    last_reset = last_reset.replace(
        hour=WEEKLY_RESET_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    if last_reset > now_local:
        last_reset = last_reset - _td(days=7)
    return last_reset.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")


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
                actual, baseline, savings,
                query_preview[:QUERY_PREVIEW_MAX_CHARS],
                topic, tenant_slug,
            ),
        )


def _exclude_clause(exclude_modos: str | None, prefix: str = "AND") -> tuple[str, list[str]]:
    """Construye un fragmento SQL `AND modo NOT IN (?,?,..)` parametrizado.
    `exclude_modos` es CSV ('claude_code,probe' o vacío). Retorna ('', []) si
    no hay nada que excluir, listo para concatenar."""
    if not exclude_modos:
        return "", []
    modos = [m.strip() for m in exclude_modos.split(",") if m.strip()]
    if not modos:
        return "", []
    placeholders = _in_clause_values(tuple(modos))
    return f" {prefix} modo NOT IN ({placeholders})", modos


def _routing_gap(where_time: str, params: Sequence[Any]) -> dict:
    """Ahorro por routing frente a baseline Sonnet."""
    placeholders = _in_clause_values(ROUTING_MODOS)
    sql = f"""
        SELECT
            COUNT(*) AS queries,
            COALESCE(SUM(actual_cost),0)    AS actual,
            COALESCE(SUM(baseline_cost),0)  AS baseline,
            COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens),0) AS total_tokens
        FROM cost_log
        WHERE modo IN ({placeholders}) {where_time}
    """
    routing = _fetch_one_dict(sql, tuple(ROUTING_MODOS) + tuple(params))
    routing["gap_usd"] = round(routing["baseline"] - routing["actual"], 4)
    routing["gap_pct"] = (
        round((routing["gap_usd"] / routing["baseline"]) * 100, 1)
        if routing["baseline"]
        else 0
    )
    return routing


def _opus_no_cache_cost(input_tokens: int, output_tokens: int, cache_tokens: int) -> float:
    """Costo Opus si cache_read_tokens se cobraran como input normal."""
    opus_price = PRICES.get(OPUS_MODEL, PRICES[BASELINE_MODEL])
    return (
        (input_tokens + cache_tokens) * opus_price["input"]
        + output_tokens * opus_price["output"]
    )


def _opus_cache_optimization(where_time: str, params: Sequence[Any]) -> dict:
    """Ahorro por prompt caching en registros de Claude Code."""
    sql = f"""
        SELECT
            COUNT(*) AS queries,
            COALESCE(SUM(actual_cost),0)        AS actual,
            COALESCE(SUM(input_tokens),0)       AS input_tokens,
            COALESCE(SUM(output_tokens),0)      AS output_tokens,
            COALESCE(SUM(cache_read_tokens),0)  AS cache_tokens
        FROM cost_log
        WHERE modo = ? {where_time}
    """
    opus = _fetch_one_dict(sql, (CLAUDE_CODE_MODO,) + tuple(params))
    if opus["queries"] > 0:
        cost_no_cache = _opus_no_cache_cost(
            opus["input_tokens"],
            opus["output_tokens"],
            opus["cache_tokens"],
        )
        opus["no_cache_cost"] = round(cost_no_cache, 4)
        opus["cache_savings_usd"] = round(cost_no_cache - opus["actual"], 4)
        opus["cache_savings_pct"] = (
            round((opus["cache_savings_usd"] / cost_no_cache) * 100, 1)
            if cost_no_cache
            else 0
        )
    else:
        opus["no_cache_cost"] = 0
        opus["cache_savings_usd"] = 0
        opus["cache_savings_pct"] = 0
    return opus


def _hours_freed(where_time: str, params: Sequence[Any]) -> dict:
    """Queries procesadas por API key y su estimación de ventanas Max liberadas."""
    sql = f"""
        SELECT COUNT(*) AS queries
        FROM cost_log
        WHERE {_offload_mode_filter()} {where_time}
    """
    row = _fetch_one_dict(sql, tuple(OFFLOAD_EXCLUDED_MODOS) + tuple(params))
    queries_processed = row["queries"] or 0
    return {
        "queries_processed": queries_processed,
        "max_quota_per_5h_window": MAX_5X_QUOTA_PER_5H,
        "windows_freed": round(queries_processed / MAX_5X_QUOTA_PER_5H, 2),
        "hours_freed_estimate": round(
            queries_processed / MAX_5X_QUOTA_PER_5H * MAX_WINDOW_HOURS,
            1,
        ),
    }


def _capacity_usage() -> dict:
    """Uso de capacidad Max 5x por día, semana y mes calendario."""
    offload_filter = _offload_mode_filter()
    week_threshold_utc = _last_weekly_reset_utc()
    used_today = _count_query(
        f"SELECT COUNT(*) AS q FROM cost_log WHERE {offload_filter} "
        f"AND date(ts, 'localtime')={LOCAL_TODAY_SQL}",
        OFFLOAD_EXCLUDED_MODOS,
    )
    used_week = _count_query(
        f"SELECT COUNT(*) AS q FROM cost_log WHERE {offload_filter} AND ts >= ?",
        tuple(OFFLOAD_EXCLUDED_MODOS) + (week_threshold_utc,),
    )
    used_month = _count_query(
        f"SELECT COUNT(*) AS q FROM cost_log WHERE {offload_filter} "
        "AND date(ts, 'localtime') >= date('now', 'start of month', 'localtime')",
        OFFLOAD_EXCLUDED_MODOS,
    )

    return {
        "plan": "Max 5x",
        "msgs_per_5h_window": MAX_5X_QUOTA_PER_5H,
        "calc_basis": f"{MAX_5X_QUOTA_PER_5H} msgs/5h × {WINDOWS_PER_DAY} vent./día",
        "daily": _period(used_today, DAILY_CAPACITY, "Hoy"),
        "weekly": _period(used_week, WEEKLY_CAPACITY, "Esta semana"),
        "monthly": _period(used_month, MONTHLY_CAPACITY, "Este mes"),
    }


def lifetime_totals(exclude_modos: str | None = None) -> dict:
    """Totales acumulados de costos, tokens y ahorro."""
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
    d = _fetch_one_dict(sql, params)
    d["savings_pct"] = _savings_pct(d["savings"], d["baseline_cost"])
    return d


def daily_summary(days: int = 7, exclude_modos: str | None = None) -> list[dict]:
    """Resumen diario de uso y costo para los últimos `days` días."""
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
    return _fetch_all(sql, params)


def by_model(exclude_modos: str | None = None) -> list[dict]:
    """Costos agrupados por modelo y modo."""
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
    return _fetch_all(sql, params)


def recent(limit: int = 20) -> list[dict]:
    """Últimos registros de cost_log en orden descendente."""
    return _fetch_all("SELECT * FROM cost_log ORDER BY id DESC LIMIT ?", (limit,))


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
          AND modo != ?
        GROUP BY modo
    """
    rows = _fetch_all(sql, (start_date, end_date, PROBE_MODO))
    totals = _sum_rows(
        rows,
        ("queries", "input_tokens", "output_tokens", "cache_tokens", "actual_cost"),
    )
    return {"start": start_date, "end": end_date, "by_modo": rows, "totals": totals}


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
    where_time, params = _roi_time_filter(days)

    return {
        "period_days": days,
        "routing": _routing_gap(where_time, params),
        "opus_cache": _opus_cache_optimization(where_time, params),
        "hours": _hours_freed(where_time, params),
        "capacity": _capacity_usage(),
    }


def weekly_comparison() -> dict:
    """Esta semana (últimos 7d) vs anterior (días -8 a -14).
    Excluye modo='external' (logs Claude Code históricos) y 'probe' (health-checks).
    """
    excluded_placeholders = _in_clause_values(NON_MOLLO_MODOS)
    base_sql = f"""
        SELECT
            COUNT(*) AS queries,
            COALESCE(SUM(input_tokens),0)      AS input_tokens,
            COALESCE(SUM(output_tokens),0)     AS output_tokens,
            COALESCE(SUM(cache_read_tokens),0) AS cache_tokens,
            COALESCE(SUM(actual_cost),0)       AS actual_cost,
            COALESCE(SUM(baseline_cost),0)     AS baseline_cost,
            COALESCE(SUM(savings),0)           AS savings
        FROM cost_log
        WHERE modo NOT IN ({excluded_placeholders})
    """
    with _db() as conn:
        this_week = dict(conn.execute(
            base_sql + " AND ts >= datetime('now','-7 days')",
            NON_MOLLO_MODOS,
        ).fetchone())
        prior_week = dict(conn.execute(
            base_sql + " AND ts >= datetime('now','-14 days') AND ts < datetime('now','-7 days')",
            NON_MOLLO_MODOS,
        ).fetchone())

    deltas = {
        k: _pct_delta(this_week[k], prior_week[k])
        for k in COMPARISON_METRICS
    }
    return {"this_week": this_week, "prior_week": prior_week, "delta_pct": deltas}


def top_queries(limit: int = 5, days: int | None = None) -> list[dict]:
    """Queries mollo_brain más costosas, opcionalmente limitadas por días."""
    # Excluye modos no-mollo_brain (external = logs importados de Claude Code
    # directo, probe = pings de health-check de /limits/probe).
    excluded_placeholders = _in_clause_values(NON_MOLLO_MODOS)
    sql = f"""
        SELECT id, ts, model, modo, input_tokens, output_tokens,
               cache_read_tokens AS cache_tokens,
               actual_cost, baseline_cost, savings,
               query_preview, topic, tenant_slug
        FROM cost_log
        WHERE modo NOT IN ({excluded_placeholders})
    """
    days_clause, params = _days_filter(days)
    sql += days_clause
    sql += " ORDER BY actual_cost DESC LIMIT ?"
    params.append(int(limit))
    return _fetch_all(sql, tuple(NON_MOLLO_MODOS) + tuple(params))


def by_topic() -> list[dict]:
    """Costos agrupados por topic."""
    return _fetch_all(f"""
            SELECT
                COALESCE(topic, '{DEFAULT_TOPIC}') AS topic,
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
        """)


def by_provider() -> list[dict]:
    """Costos agrupados por proveedor inferido desde el modelo."""
    return _fetch_all(f"""
            SELECT
                CASE
                    WHEN model LIKE 'gpt%'    THEN '{PROVIDER_OPENAI}'
                    WHEN model LIKE 'claude%' THEN '{PROVIDER_ANTHROPIC}'
                    WHEN model LIKE 'gemini%' THEN '{PROVIDER_GOOGLE}'
                    ELSE '{PROVIDER_OTHER}'
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
        """)


def topic_by_model() -> list[dict]:
    """Matriz topic × model para el heatmap del dashboard."""
    return _fetch_all(f"""
            SELECT
                COALESCE(topic, '{DEFAULT_TOPIC}') AS topic,
                model,
                COUNT(*)         AS queries,
                SUM(input_tokens + output_tokens) AS total_tokens,
                SUM(actual_cost) AS actual_cost,
                SUM(savings)     AS savings
            FROM cost_log
            GROUP BY topic, model
            ORDER BY topic, queries DESC
        """)


def by_tenant() -> list[dict]:
    """Costo total agrupado por tenant_slug (mes actual y lifetime)."""
    return _fetch_all(f"""
            SELECT
                COALESCE(tenant_slug, '{INTERNAL_TENANT}') AS tenant_slug,
                COUNT(*)                              AS queries,
                SUM(input_tokens)                     AS input_tokens,
                SUM(output_tokens)                    AS output_tokens,
                SUM(cache_read_tokens)                AS cache_tokens,
                SUM(actual_cost)                      AS actual_cost,
                SUM(baseline_cost)                    AS baseline_cost,
                SUM(savings)                          AS savings,
                SUM(CASE WHEN ts >= {MONTH_START_SQL}
                         THEN actual_cost ELSE 0 END) AS cost_this_month,
                SUM(CASE WHEN ts >= {MONTH_START_SQL}
                         THEN 1 ELSE 0 END)           AS queries_this_month
            FROM cost_log
            GROUP BY tenant_slug
            ORDER BY actual_cost DESC
        """)


def by_tenant_model() -> list[dict]:
    """Desglose por tenant × modelo — para análisis costo-beneficio en admin."""
    return _fetch_all(f"""
            SELECT
                COALESCE(tenant_slug, '{INTERNAL_TENANT}')       AS tenant_slug,
                CASE
                    WHEN model LIKE 'gpt-4o%' THEN 'GPT-4o'
                    ELSE model
                END                                         AS provider,
                model,
                COUNT(*)                                    AS queries,
                SUM(input_tokens)                           AS input_tokens,
                SUM(output_tokens)                          AS output_tokens,
                SUM(cache_read_tokens)                      AS cache_tokens,
                SUM(actual_cost)                            AS actual_cost,
                SUM(CASE WHEN ts >= {MONTH_START_SQL}
                         THEN actual_cost ELSE 0 END)       AS cost_this_month,
                SUM(CASE WHEN ts >= {MONTH_START_SQL}
                         THEN input_tokens + output_tokens ELSE 0 END) AS tokens_this_month
            FROM cost_log
            WHERE tenant_slug IS NOT NULL AND tenant_slug != '{INTERNAL_TENANT}'
            GROUP BY tenant_slug, model
            ORDER BY tenant_slug, actual_cost DESC
        """)


def session_totals(since_ts: str) -> dict:
    """Totales de sesión desde un timestamp ISO."""
    d = _fetch_one_dict("""
            SELECT
                COUNT(*)         AS queries,
                SUM(input_tokens + output_tokens) AS total_tokens,
                SUM(actual_cost)   AS actual_cost,
                SUM(baseline_cost) AS baseline_cost,
                SUM(savings)       AS savings
            FROM cost_log WHERE ts >= ?
        """, (since_ts,))
    d["savings_pct"] = _savings_pct(d["savings"], d["baseline_cost"])
    return d
