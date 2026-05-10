"""
Auto-load del CLAUDE.md personal de Adolfo desde Dropbox vault.

CLAUDE.md vive en /Obsidian/vault/CLAUDE.md y contiene contexto que el
usuario edita semanalmente: proyectos activos, stuck on, próximo milestone,
qué está leyendo. Este módulo lo lee con cache TTL 5min y lo expone como
bloque para inyectar al system prompt de Claude/OpenAI/Gemini.

Anthropic cachea por bloque: MOLLO_SYSTEM (estable, casi siempre cached) +
CLAUDE.md (cambia semanal, se re-cachea entonces). El usuario NO paga por
re-procesar el contexto en queries normales.

Uso:
    from user_context_service import get_user_claude_md
    text = get_user_claude_md()  # str, cached 5min

Para forzar refresh (después de que el user edite explícitamente):
    text = get_user_claude_md(force_refresh=True)
"""
import time
import logging

logger = logging.getLogger(__name__)

VAULT_CLAUDE_MD_PATH = "/Obsidian/vault/CLAUDE.md"
CACHE_TTL_SEC        = 300  # 5 min
MAX_LEN_CHARS        = 8000  # cap para evitar payload exagerado al system

_cache: dict = {"text": None, "ts": 0.0}


def _fetch_from_dropbox() -> str:
    """Lee CLAUDE.md de Dropbox. Retorna '' si falla (no rompe el chat)."""
    try:
        from dropbox_service import descargar_texto
        text, _ = descargar_texto(VAULT_CLAUDE_MD_PATH)
        if text and not text.startswith("Error"):
            if len(text) > MAX_LEN_CHARS:
                logger.warning(
                    "CLAUDE.md mide %d chars, truncando a %d",
                    len(text), MAX_LEN_CHARS,
                )
                text = text[:MAX_LEN_CHARS] + "\n\n[...truncado por longitud]"
            return text
    except Exception as e:
        logger.warning("CLAUDE.md fetch falló: %s", e)
    return ""


def get_user_claude_md(force_refresh: bool = False) -> str:
    """Devuelve contenido de CLAUDE.md, cacheado 5 min en memoria.
    Retorna '' si no se puede leer — no romper el chat por esto."""
    now = time.monotonic()
    if not force_refresh and _cache["text"] is not None and (now - _cache["ts"]) < CACHE_TTL_SEC:
        return _cache["text"]
    text = _fetch_from_dropbox()
    _cache["text"] = text
    _cache["ts"] = now
    return text


def invalidate_cache():
    """Forzar re-fetch en la próxima llamada. Útil para endpoint de admin."""
    _cache["ts"] = 0.0


def get_user_claude_md_section() -> str:
    """Como get_user_claude_md pero envuelto con header explícito para inyectar
    al system prompt. Si CLAUDE.md está vacío/no existe, devuelve ''."""
    text = get_user_claude_md()
    if not text.strip():
        return ""
    return (
        "═══ CONTEXTO PERSONAL DEL USUARIO (CLAUDE.md) ═══\n"
        "Este es contexto vivo que Adolfo edita semanalmente desde su Obsidian vault. "
        "Tiene prioridad sobre datos viejos en memoria si hay conflicto.\n\n"
        f"{text}\n"
        "═══ FIN CONTEXTO PERSONAL ═══"
    )
