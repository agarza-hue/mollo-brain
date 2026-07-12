# Mollo Brain — Instrucciones para Claude Code

## Qué es este repo
FastAPI en `/root/mollo_brain/` que sirve como backend de:
1. **Mollo** — asistente personal de Adolfo (memoria semántica, RAG, routing de modelos)
2. **SinergyOS** — SaaS multi-tenant que expone la misma API a clientes externos

## Stack
- Python 3.12, venv en `/root/venv/`
- FastAPI + uvicorn, systemd: `mollo-brain`, puerto 8002
- PostgreSQL: `molloai_postgres` puerto 5434, DB `molloai`
- Qdrant puerto 6333 (vectores), SQLite `~/.mollo/costs.db` (costos)
- Logs: `/var/log/mollo_brain.log`

## Comandos críticos
```bash
systemctl restart mollo-brain   # siempre después de cambiar código
systemctl is-active mollo-brain # verificar
tail -f /var/log/mollo_brain.log
/root/venv/bin/pip install <pkg> # instalar dependencias
```

## Routing de modelos (NO cambiar sin razón)
- `simple` → GPT-4o-mini (barato, rápido)
- `medio` → GPT-4o
- `complejo` → Claude Sonnet 4.6
- `agente` → GPT-4o + tools

## Reglas
- Nunca usar `--no-verify` en git
- Al agregar endpoint nuevo: registrar router en `main.py`
- Background tasks: `increment_usage` ANTES de `_save_in_background`
- Tenant `adolfo` y `sinergy-local`: protegidos, no eliminar
- No commitear `.env`, `mollo_memory.json`, `mollo_topics.json`, `*.bak`, `.agents/`, `.claude/`
- Stripe SDK v15: re-parsear raw JSON tras `construct_event` (devuelve StripeObject)
- SMTP bloqueado en VPS → usar Resend HTTP API

## CLI (`mollo_cli.py`)
REPL contra Brain en `:8002`. Comandos relevantes:
- `/write <ruta>` — escribe un archivo de forma interactiva: pega el contenido,
  termina con una línea sola `EOF` (o Ctrl-D). Muestra **preview de diff** (estilo
  Claude Code: `±` con números de línea), pide confirmación `[y/N]`, y guarda un
  backup `<archivo>.bak.<timestamp>` antes de sobreescribir un archivo existente.

### Tool-events (streaming brain → CLI)
Las tools del agente emiten eventos estructurados aparte del resultado que ve el LLM:
- `tools_service.begin_tool_events()` se llama al inicio de cada `stream_agent_*`;
  las tools usan `_push_tool_event(ev)` y el stream drena con `drain_tool_events()`.
- Cada evento viaja al CLI como un frame `\x05{json}\n` (separado del texto y del
  frame de usage `\x03{json}`). El CLI tolera frames partidos entre chunks.
- Evento `write` (lo emite `_escribir_archivo`) lleva la diff calculada
  (`diff_lines`/`new_preview`, `added`/`removed`, truncado a 200 líneas) y el CLI
  lo renderiza como panel. Al agregar tools que modifiquen estado, emite un evento
  aquí en vez de meter el detalle en el string de resultado del LLM.

## Estructura de routers
| Archivo | Prefix | Propósito |
|---|---|---|
| `routers/chat.py` | `/chat` | /ask, /stream |
| `routers/documents.py` | `/docs` | upload, list, delete |
| `routers/tenants.py` | `/sinergy` | CRUD tenants + /register |
| `routers/billing.py` | `/sinergy/billing` | Stripe webhook |
| `routers/costs.py` | `/costs` | métricas de costos |
| `insforge.py` | — | middleware multi-tenant |
