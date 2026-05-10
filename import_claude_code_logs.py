"""Importa transcripts de Claude Code (sincronizados via Syncthing desde Windows)
al cost_log para visualización unificada en el dashboard de mollo-web.

Idempotente: usa requestId/uuid como external_id con UNIQUE constraint.
Re-correrlo es seguro y eficiente — sólo inserta turnos nuevos.

Estructura asumida del JSONL (Claude Code v2):
- 1 línea por turno
- Top-level: { type, timestamp, requestId, uuid, sessionId, cwd, message }
- message.usage: { input_tokens, output_tokens, cache_read_input_tokens,
                   cache_creation_input_tokens }
- Sólo turnos type='assistant' con message.usage tienen tokens
"""
import json
import sqlite3
import os
import sys
from pathlib import Path

# Permite invocar como script: from project root
sys.path.insert(0, '/root/mollo_brain')
from cost_service import compute_cost, compute_baseline, _normalize

PROJECTS_DIR = Path("/home/adolfo/claude-projects")
DB_PATH      = os.path.expanduser("~/.mollo/costs.db")

MODO       = "claude_code"
TENANT     = "claude_code"   # opcional; los demás modos usan None
TOPIC_BASE = "claude_code"


def _topic_from_cwd(cwd: str) -> str:
    """Deriva un topic legible desde el cwd del proyecto.
    'C:\\Users\\agarz\\Dropbox\\Git\\Claude' → 'claude_code:Claude'
    """
    if not cwd:
        return TOPIC_BASE
    leaf = Path(cwd.replace("\\", "/")).name or TOPIC_BASE
    return f"{TOPIC_BASE}:{leaf[:30]}"


def parse_jsonl(path: Path):
    """Yields (external_id, ts, model, in_tok, out_tok, cache_read, cwd, preview)
    for cada turno con usage."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            u   = msg.get("usage") or {}
            if not u:
                continue
            in_tok      = int(u.get("input_tokens") or 0)
            cache_read  = int(u.get("cache_read_input_tokens") or 0)
            cache_create= int(u.get("cache_creation_input_tokens") or 0)
            # cache_creation NO se factura como cache_read en Anthropic — se cobra
            # como input write (tarifa premium 25% extra). Para no complicar,
            # lo metemos como input_tokens regular (subestima ahorro pero conserva
            # paridad con la tarifa real que pagaste).
            out_tok     = int(u.get("output_tokens") or 0)
            model       = msg.get("model") or "claude-sonnet-4-6"
            ts          = d.get("timestamp") or ""
            ext_id      = d.get("requestId") or d.get("uuid")
            if not ext_id:
                continue
            cwd     = d.get("cwd", "")
            session = d.get("sessionId", "")[:8]
            preview = f"Claude Code · {Path(cwd.replace(chr(92),'/')).name if cwd else '?'} · {session}"
            yield {
                "external_id": ext_id,
                "ts":          ts,
                "model":       model,
                "input":       in_tok + cache_create,
                "output":      out_tok,
                "cache_read":  cache_read,
                "cwd":         cwd,
                "preview":     preview,
            }


def main():
    if not PROJECTS_DIR.exists():
        print(f"ABORT: {PROJECTS_DIR} no existe")
        sys.exit(1)

    files = list(PROJECTS_DIR.rglob("*.jsonl"))
    print(f"Archivos JSONL encontrados: {len(files)}")

    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    skipped_dupe = 0
    skipped_nousage = 0
    total_seen = 0

    for fp in files:
        for rec in parse_jsonl(fp):
            total_seen += 1
            model_norm = _normalize(rec["model"])
            actual = compute_cost(model_norm, rec["input"], rec["output"], rec["cache_read"])
            baseline = compute_baseline(rec["input"], rec["output"], rec["cache_read"])
            savings = baseline - actual
            try:
                conn.execute(
                    """INSERT INTO cost_log
                       (ts, model, modo, input_tokens, output_tokens, cache_read_tokens,
                        actual_cost, baseline_cost, savings, query_preview, topic, tenant_slug,
                        external_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rec["ts"], model_norm, MODO,
                        rec["input"], rec["output"], rec["cache_read"],
                        actual, baseline, savings,
                        rec["preview"], _topic_from_cwd(rec["cwd"]), TENANT,
                        rec["external_id"],
                    )
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # external_id duplicate — already imported
                skipped_dupe += 1

    conn.commit()
    conn.close()

    print(f"  turnos visitados : {total_seen}")
    print(f"  insertados nuevos: {inserted}")
    print(f"  ya existían      : {skipped_dupe}")


if __name__ == "__main__":
    main()
