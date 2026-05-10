# Mollo systemd units

Snapshot de las units instaladas en `/etc/systemd/system/` del VPS de producción.
Versionadas para reproducibilidad del setup en caso de reinstalación o fork.

## Inventario

| Unit | Tipo | Trigger | Working dir | Función |
|---|---|---|---|---|
| `mollo-brain.service` | servicio long-running | always | `/root/mollo_brain/` | FastAPI puerto 8002 — backend principal |
| `mollo-briefing.service` + `.timer` | oneshot + timer | 06:00 CDMX diario | `/root/mollo_brain/` | Daily brief vía Telegram (Claude Sonnet) |
| `mollo-claude-import.service` + `.timer` | oneshot + timer | cada 5 min | `/root/mollo_brain/` | Import transcripts Claude Code → cost_log |
| `mollo-gateway.service` | servicio long-running | always | `/opt/mollo-gateway/` | Gateway puerto 8100 (NO está en este repo) |
| `mollo-telegram.service` | servicio long-running | always | `/opt/mollo-telegram/` | Bot Telegram `@mollo_adolfo_bot` (NO está en este repo) |
| `mollo-autonomo.service` | servicio long-running | always | `/opt/mollo-telegram/` | Tareas autónomas + alertas VPS |
| `mollo-monitor.service` | servicio long-running | always | `/opt/mollo-gateway/` | Monitor VPS |
| `mollo-auto-ingest.service` | servicio long-running | always | `/opt/mollo-gateway/` | Auto-ingest worker |

## Dependencias externas

Los servicios `mollo-brain`, `mollo-briefing`, `mollo-claude-import` corren con
`/root/venv/` (Python 3.12) y este repo en `/root/mollo_brain/`.

Los servicios `mollo-gateway`, `mollo-monitor`, `mollo-auto-ingest` viven en
`/opt/mollo-gateway/` (repo separado, NO incluido aquí).

Los servicios `mollo-telegram`, `mollo-autonomo` viven en `/opt/mollo-telegram/`
(repo separado, NO incluido aquí).

## Variables de entorno

`mollo-brain` y `mollo-briefing` leen `/root/mollo_brain/.env` (ver `.env.example`
si existe, o reconstruir con: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`,
`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `BANXICO_TOKEN`, `DROPBOX_*`,
`MOLLOIA_STRIPE_*`, `RESEND_API_KEY`).

`mollo-briefing` también lee `/opt/mollo-telegram/.env` para `TELEGRAM_TOKEN`
y `ALLOWED_USER_ID` (chat_id del usuario admin).

## Instalación / re-instalación

```bash
# 1. Copia las units al sistema
sudo cp /root/mollo_brain/deployment/systemd/mollo-*.{service,timer} /etc/systemd/system/

# 2. Recarga systemd
sudo systemctl daemon-reload

# 3. Habilita + arranca las que apliquen (ejemplo: brain + briefing)
sudo systemctl enable --now mollo-brain.service
sudo systemctl enable --now mollo-briefing.timer
sudo systemctl enable --now mollo-claude-import.timer

# 4. Verifica
systemctl status mollo-brain.service
systemctl list-timers mollo-*.timer
```

## Verificar próximas ejecuciones

```bash
systemctl list-timers --all 'mollo-*'
```

## Logs

| Unit | Log path |
|---|---|
| mollo-brain | `/var/log/mollo_brain.log` |
| mollo-briefing | `/var/log/mollo-briefing.log` |
| mollo-claude-import | `journalctl -u mollo-claude-import` |
| Resto | `journalctl -u <unit-name>` |

## Notas

- `mollo-brain.service` tiene `--timeout-graceful-shutdown 15` y `RestartSec=1`
  para downtime mínimo (~16s peor caso) durante deploys. Documentado en
  `project_mollo.md` (memoria) bajo "ESTADO AL 2026-05-09 — IA COST OPTIMIZATION".
- `mollo-briefing.timer` usa `Persistent=true` para hacer catch-up si el VPS
  estuvo dormido al momento del trigger 06:00.
- `mollo-claude-import.timer` corre cada 5 min para mantener el dashboard ROI
  fresco con el gasto real de Claude Code (vía Syncthing desde Windows).
