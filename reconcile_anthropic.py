"""Reconciliación mensual: cost_log vs export oficial de Anthropic Console.

Compara lo que mollo_brain registró contra lo que Anthropic reporta como
real. Detecta drift en tokens y en cost, y reporta requests faltantes
(que mollo_brain no capturó) o extras (que mollo_brain registró pero no
están en Anthropic).

Uso:
  /root/venv/bin/python reconcile_anthropic.py <path-to-csv-or-tsv>

El export de Anthropic Console (Logs → Export) viene como TSV con
line-wrap (input/output tokens en líneas separadas). Mismo parser que
import_anthropic_logs.py.

Persiste resultado en tabla `reconciliations` para historial:
  - period_start, period_end, source_file
  - total_requests_csv, total_requests_log
  - total_cost_csv, total_cost_log, drift_usd, drift_pct
  - missing_count, extra_count
  - status: 'ok' (drift <5%) | 'warn' (5-10%) | 'fail' (>10%)
"""
import sys, re, sqlite3, os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, '/root/mollo_brain')
from cost_service import compute_cost, _normalize, PRICES  # noqa

DB_PATH = os.path.expanduser("~/.mollo/costs.db")


def _ensure_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reconciliations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            source_file TEXT,
            period_start TEXT,
            period_end TEXT,
            total_requests_csv INTEGER,
            total_requests_log INTEGER,
            total_cost_csv REAL,
            total_cost_log REAL,
            drift_usd REAL,
            drift_pct REAL,
            missing_count INTEGER,
            extra_count INTEGER,
            status TEXT,
            details_json TEXT
        )
    """)
    conn.commit()
    conn.close()


def parse_anthropic_export(path: Path) -> list[dict]:
    """Parsea TSV con line-wrap: 1 record cada 2 líneas."""
    text = path.read_text()
    # Header: Time (CST)\tID\tModel\tInput Tokens\tOutput Tokens\tType\tService Tier\tRequest
    pattern = re.compile(
        r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\t(req_\w+)\t([^\t]+)\t\n(\d+)\n(\d+)\t([^\t]*)\t([^\t]*)\t?',
        re.MULTILINE
    )
    out = []
    for m in pattern.finditer(text):
        ts, req_id, model, in_tok, out_tok, typ, tier = m.groups()
        out.append({
            "request_id": req_id,
            "ts":        ts,  # CST timezone naive
            "model":     model.strip(),
            "input":     int(in_tok),
            "output":    int(out_tok),
        })
    return out


def reconcile(csv_path: str) -> dict:
    p = Path(csv_path)
    if not p.exists():
        print(f"ABORT: {csv_path} no existe")
        sys.exit(1)

    _ensure_table()
    csv_records = parse_anthropic_export(p)
    if not csv_records:
        print("ABORT: 0 records parseados del CSV — formato no esperado?")
        sys.exit(2)

    # Período cubierto por el CSV
    timestamps = [r["ts"] for r in csv_records]
    period_start = min(timestamps)
    period_end   = max(timestamps)
    print(f"CSV: {len(csv_records)} requests · {period_start} → {period_end}")

    # Build lookup por request_id
    csv_by_id = {r["request_id"]: r for r in csv_records}

    # Pull cost_log filas con external_id en ese rango (más amplio en UTC)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    log_rows = conn.execute("""
        SELECT external_id, ts, model, input_tokens, output_tokens,
               cache_read_tokens, actual_cost, modo
        FROM cost_log
        WHERE external_id IS NOT NULL
          AND ts >= ? AND ts <= ?
    """, (period_start.replace(' ', 'T'), period_end.replace(' ', 'T') + 'Z')).fetchall()
    log_by_id = {r["external_id"]: dict(r) for r in log_rows}
    print(f"cost_log: {len(log_by_id)} requests con external_id en ese rango")

    # Compare
    matched = []
    missing = []  # in CSV but not in cost_log
    cost_csv_total = 0.0
    cost_log_total = 0.0
    tokens_csv_in = tokens_csv_out = 0
    tokens_log_in = tokens_log_out = 0

    for req_id, csv_rec in csv_by_id.items():
        # Computar costo según nuestras tarifas (lo que Anthropic SÍ cobra)
        norm = _normalize(csv_rec["model"])
        csv_cost = compute_cost(norm, csv_rec["input"], csv_rec["output"], 0)
        cost_csv_total += csv_cost
        tokens_csv_in  += csv_rec["input"]
        tokens_csv_out += csv_rec["output"]

        log_rec = log_by_id.get(req_id)
        if log_rec is None:
            missing.append({
                "request_id": req_id,
                "ts": csv_rec["ts"],
                "model": csv_rec["model"],
                "csv_cost": csv_cost,
            })
        else:
            cost_log_total += log_rec["actual_cost"] or 0
            tokens_log_in  += log_rec["input_tokens"] or 0
            tokens_log_out += log_rec["output_tokens"] or 0
            # Drift por record
            tok_drift_in  = abs(csv_rec["input"]  - (log_rec["input_tokens"] or 0))
            tok_drift_out = abs(csv_rec["output"] - (log_rec["output_tokens"] or 0))
            matched.append({
                "request_id": req_id,
                "csv_cost": csv_cost,
                "log_cost": log_rec["actual_cost"],
                "tok_drift_in": tok_drift_in,
                "tok_drift_out": tok_drift_out,
            })

    # extra = en cost_log pero no en CSV (sólo de modos importables)
    csv_ids = set(csv_by_id.keys())
    extras = [r for rid, r in log_by_id.items() if rid not in csv_ids]

    # Drift
    drift_usd = cost_log_total - cost_csv_total
    drift_pct = (drift_usd / cost_csv_total * 100) if cost_csv_total else 0
    abs_pct = abs(drift_pct)
    if abs_pct < 5:
        status = "ok"
    elif abs_pct < 10:
        status = "warn"
    else:
        status = "fail"

    result = {
        "source_file": str(p.name),
        "period_start": period_start,
        "period_end": period_end,
        "total_requests_csv": len(csv_records),
        "total_requests_log": len(matched),
        "total_cost_csv": round(cost_csv_total, 4),
        "total_cost_log": round(cost_log_total, 4),
        "drift_usd": round(drift_usd, 4),
        "drift_pct": round(drift_pct, 2),
        "missing_count": len(missing),
        "extra_count": len(extras),
        "status": status,
        "tokens_csv_in":  tokens_csv_in,
        "tokens_csv_out": tokens_csv_out,
        "tokens_log_in":  tokens_log_in,
        "tokens_log_out": tokens_log_out,
    }

    # Persist
    import json
    conn.execute("""
        INSERT INTO reconciliations
            (run_at, source_file, period_start, period_end,
             total_requests_csv, total_requests_log,
             total_cost_csv, total_cost_log, drift_usd, drift_pct,
             missing_count, extra_count, status, details_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now(timezone.utc).isoformat(timespec='seconds'),
        result["source_file"], period_start, period_end,
        result["total_requests_csv"], result["total_requests_log"],
        result["total_cost_csv"], result["total_cost_log"],
        result["drift_usd"], result["drift_pct"],
        result["missing_count"], result["extra_count"],
        status, json.dumps({"missing": missing[:20], "extras": [e["external_id"] for e in extras[:20]]}),
    ))
    conn.commit()
    conn.close()

    # Print report
    print()
    print("═" * 70)
    print(f"  RECONCILIACIÓN · {p.name}")
    print(f"  Periodo: {period_start} → {period_end}")
    print("═" * 70)
    print(f"  Requests CSV (Anthropic): {result['total_requests_csv']:>6}")
    print(f"  Requests cost_log match:  {result['total_requests_log']:>6}")
    print(f"  Faltantes (en CSV no log):{result['missing_count']:>6}")
    print(f"  Extras (en log no CSV):   {result['extra_count']:>6}")
    print()
    print(f"  Tokens CSV  in/out: {tokens_csv_in:>10,} / {tokens_csv_out:>10,}")
    print(f"  Tokens log  in/out: {tokens_log_in:>10,} / {tokens_log_out:>10,}")
    print()
    print(f"  Cost CSV (Anthropic):  ${result['total_cost_csv']:.4f}")
    print(f"  Cost log (mollo_brain): ${result['total_cost_log']:.4f}")
    print(f"  Drift USD:              ${result['drift_usd']:+.4f}")
    print(f"  Drift %:                {result['drift_pct']:+.2f}%")
    print()
    icon = {'ok': '✅', 'warn': '🟡', 'fail': '🔴'}[status]
    print(f"  {icon} STATUS: {status.upper()} (threshold: ok<5%, warn<10%)")
    print("═" * 70)

    if missing[:5]:
        print(f"\n  Primeras {min(5,len(missing))} faltantes:")
        for m in missing[:5]:
            print(f"    {m['request_id']}  {m['ts']}  {m['model']}  ${m['csv_cost']:.4f}")

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: reconcile_anthropic.py <csv_path>")
        sys.exit(1)
    reconcile(sys.argv[1])
