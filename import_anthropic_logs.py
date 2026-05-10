"""One-shot import: poblar cost_log con logs históricos de console.anthropic.com.

Estos son requests al API de Anthropic hechos antes de que mollo_brain
empezara a trackear (i.e. desde Claude Code directo). Los importamos con
flag distinguible (modo='external', topic='claude_code') para que aparezcan
en el dashboard pero NO contaminen los promedios de mollo_brain.
"""
import json, sqlite3, os, sys
from datetime import datetime
from cost_service import compute_cost, _normalize, BASELINE_MODEL

DB = os.path.expanduser('~/.mollo/costs.db')
JSON_PATH = '/root/claude_logs_parsed.json'

MODO_FLAG  = 'external'
TOPIC_FLAG = 'claude_code'
PREVIEW    = 'Anthropic API log (Claude Code directo)'


def main():
    with open(JSON_PATH) as f:
        records = json.load(f)
    print(f'Records a importar: {len(records)}')

    conn = sqlite3.connect(DB)

    existing = conn.execute(
        "SELECT COUNT(*) FROM cost_log WHERE topic = ? OR modo = ?",
        (TOPIC_FLAG, MODO_FLAG)
    ).fetchone()[0]
    if existing > 0:
        print(f'ABORT: ya hay {existing} rows con topic={TOPIC_FLAG} o modo={MODO_FLAG}.')
        print('Si quieres reimportar, borra primero:')
        print(f'  DELETE FROM cost_log WHERE topic = "{TOPIC_FLAG}" OR modo = "{MODO_FLAG}";')
        sys.exit(1)

    inserted = 0
    skipped = 0
    for r in records:
        model_norm = _normalize(r['model'])
        if model_norm not in {'claude-sonnet-4-6', 'claude-haiku-4-5', 'claude-opus-4-7'}:
            skipped += 1
            continue
        in_tok  = int(r['in'])
        out_tok = int(r['out'])
        actual = compute_cost(model_norm, in_tok, out_tok, 0)
        baseline = compute_cost(BASELINE_MODEL, in_tok, out_tok, 0)
        savings = baseline - actual

        ts_iso = r['ts'].replace(' ', 'T') + '+00:00'

        try:
            conn.execute(
                """INSERT INTO cost_log
                   (ts, model, modo, input_tokens, output_tokens, cache_read_tokens,
                    actual_cost, baseline_cost, savings, query_preview, topic, tenant_slug,
                    external_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts_iso, model_norm, MODO_FLAG, in_tok, out_tok, 0,
                 actual, baseline, savings, PREVIEW, TOPIC_FLAG, None,
                 r['id'])  # request_id (req_xxx) → external_id para reconciliación
            )
            inserted += 1
        except sqlite3.IntegrityError:
            # external_id ya existe — re-import idempotente
            pass

    conn.commit()
    conn.close()
    print(f'  insertados: {inserted}')
    print(f'  saltados (modelo no soportado): {skipped}')


if __name__ == '__main__':
    main()
