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
    "claude-haiku-4-5":       "claude-haiku-4-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
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


def lifetime_totals() -> dict:
    with _db() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)         AS queries,
                SUM(input_tokens)      AS input_tokens,
                SUM(output_tokens)     AS output_tokens,
                SUM(cache_read_tokens) AS cache_tokens,
                SUM(actual_cost)       AS actual_cost,
                SUM(baseline_cost)     AS baseline_cost,
                SUM(savings)           AS savings
            FROM cost_log
        """).fetchone()
    d = dict(row)
    d["savings_pct"] = round(d["savings"] / d["baseline_cost"] * 100, 1) if d["baseline_cost"] else 0
    return d


def daily_summary(days: int = 7) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(f"""
            SELECT
                DATE(ts) AS day,
                COUNT(*) AS queries,
                SUM(input_tokens + output_tokens) AS total_tokens,
                SUM(actual_cost)   AS actual_cost,
                SUM(baseline_cost) AS baseline_cost,
                SUM(savings)       AS savings
            FROM cost_log
            WHERE ts >= DATE('now', '-{days} days')
            GROUP BY day
            ORDER BY day DESC
        """).fetchall()
    return [dict(r) for r in rows]


def by_model() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("""
            SELECT
                model,
                modo,
                COUNT(*) AS queries,
                SUM(input_tokens)  AS input_tokens,
                SUM(output_tokens) AS output_tokens,
                SUM(actual_cost)   AS actual_cost,
                SUM(baseline_cost) AS baseline_cost,
                SUM(savings)       AS savings
            FROM cost_log
            GROUP BY model, modo
            ORDER BY queries DESC
        """).fetchall()
    return [dict(r) for r in rows]


def recent(limit: int = 20) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM cost_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
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
