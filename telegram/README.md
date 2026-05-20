# Mollo Telegram Bot

Bot de Telegram (`@mollo_adolfo_bot`) que es el front móvil de Mollo Brain.
Habla con Brain en `http://127.0.0.1:8002` (RAG + memoria + routing de modelos).

## Fuente de verdad
Estos archivos del repo **son** los que corren en producción. `/opt/mollo-telegram/`
contiene **symlinks** que apuntan aquí:

```
/opt/mollo-telegram/bot.py      -> /root/mollo_brain/telegram/bot.py
/opt/mollo-telegram/noticias.py -> /root/mollo_brain/telegram/noticias.py
```

Editá aquí y commiteá; el cambio queda live tras `systemctl restart mollo-telegram`.

## Qué NO está en git (vive solo en `/opt/mollo-telegram/`)
- `venv/` — virtualenv con deps (`python-telegram-bot`, `dropbox`, `qdrant-client`…).
- `.env` — secrets: `TELEGRAM_TOKEN`, `ALLOWED_USER_ID`, `OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `OLLAMA_MODEL`, etc. `bot.py` los carga con `load_dotenv()`
  desde el CWD del servicio (`WorkingDirectory=/opt/mollo-telegram`).

## Servicios systemd
- `mollo-telegram.service` → `bot.py` (este bot).
- `mollo-autonomo.service` → `tareas.py` (scheduler; también importa `noticias.py`).
  `tareas.py` sigue viviendo solo en `/opt` (aún no traído al repo).

## Operación
```bash
systemctl restart mollo-telegram     # aplicar cambios
systemctl is-active mollo-telegram
journalctl -u mollo-telegram -f
```

## Comandos del bot
Manejados por prefijo de texto en `handle_message`. Incluye `/start /docs /stats
/modo /status /reporte /vps /confirmar /cancelar /cmd /aprende /memoria /temas
/briefing /noticias /sync_dropbox /claude /openai /mollo`, más chat libre.
- `/stats` — dashboard de costos y ahorro del auto-routing (lee `/costs/*` de Brain).
- `/modo <simple|medio|complejo|agente|rapido|auto>` — fija el tier de routing por
  chat (en `context.user_data`); lo respetan los mensajes libres.
