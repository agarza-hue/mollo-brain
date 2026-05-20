"""
ContainmentGuard — port del hook PAI/ContainmentGuard.

Idea original: Daniel Miessler bloquea por código que strings sensibles
(API keys, identidad, IDs de infra) leakeen a archivos públicos. En Mollo
el equivalente es: NUNCA persistir secretos o PII en destinos que puedan
ser leídos por terceros (logs públicos, respuestas API, embeddings que
viajan a proveedores externos).

Zonas:
    TRUSTED  — destinos cifrados/locales del usuario (memoria privada, BD)
    PUBLIC   — destinos que pueden leakearse (logs stdout, respuestas API,
               embeddings de proveedores cloud, error messages)

Patrones por defecto: API keys de proveedores comunes, JWT, tokens HDHR.
Extiende `IDENTITY_PATTERNS` o `SECRET_REGEXES` con lo específico del user.

Uso:
    from containment_guard import assert_safe, Zone
    assert_safe(content, Zone.PUBLIC, label="error response")   # raises si match

O como decorador:
    @guard_output(Zone.PUBLIC)
    def format_error(e): return str(e)
"""
import re
from enum import Enum
from typing import Callable, Any
from functools import wraps


class Zone(str, Enum):
    TRUSTED = "trusted"   # mollo_memory.json, Postgres, ~/.mollo/
    PUBLIC = "public"     # logs, API responses, embeddings cloud-bound


class ContainmentError(Exception):
    """Se intentó escribir contenido sensible a zona PUBLIC."""


# Strings literales que NUNCA deben salir a PUBLIC.
# Edita ~/.mollo/containment_patterns.txt (una línea por patrón) para
# extender sin tocar código.
IDENTITY_PATTERNS: list[str] = [
    # Tokens y secrets conocidos del stack actual
    "f5f25870205298a361380ef583443947",  # HDHR token IPTV-Manager
    "cfb93c64a7be933cfb268f479122575f",  # TMDb API key
]

# Patrones regex de secretos genéricos (proveedores comunes).
SECRET_REGEXES: list[re.Pattern] = [
    re.compile(r"sk-[A-Za-z0-9]{32,}"),                       # OpenAI
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{40,}"),                # Anthropic
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),                   # Google
    re.compile(r"gsk_[A-Za-z0-9]{40,}"),                      # Groq
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\."),  # JWT
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),                      # GitHub PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),             # Slack
]


def _load_user_patterns() -> list[str]:
    """Lee patrones extra desde ~/.mollo/containment_patterns.txt si existe."""
    from pathlib import Path
    p = Path.home() / ".mollo" / "containment_patterns.txt"
    if not p.exists():
        return []
    return [line.strip() for line in p.read_text().splitlines() if line.strip() and not line.startswith("#")]


def find_violation(content: str) -> str | None:
    """Devuelve el patrón ofensor si encuentra match, sino None."""
    if not isinstance(content, str) or not content:
        return None
    for pat in IDENTITY_PATTERNS + _load_user_patterns():
        if pat and pat in content:
            return pat
    for rx in SECRET_REGEXES:
        m = rx.search(content)
        if m:
            return m.group()[:20] + "…"
    return None


def assert_safe(content: str, zone: Zone, label: str = "") -> None:
    """Lanza ContainmentError si `content` viola la zona.

    Zona TRUSTED siempre OK (escribir secretos a memoria local del user es válido).
    Zona PUBLIC: scan + raise si match.
    """
    if zone == Zone.TRUSTED:
        return
    hit = find_violation(content)
    if hit:
        raise ContainmentError(
            f"[ContainmentGuard] BLOCKED: contenido enviado a zona PUBLIC "
            f"({label or 'unlabeled'}) matchea patrón '{hit}'. "
            f"Reroutea a TRUSTED, redacta el secreto, o agrégalo a "
            f"~/.mollo/containment_patterns.txt si es false positive."
        )


def redact(content: str) -> str:
    """Versión soft: reemplaza secretos por [REDACTED] en vez de bloquear.
    Útil para logs donde quieres preservar el resto del contenido."""
    out = content
    for pat in IDENTITY_PATTERNS + _load_user_patterns():
        if pat:
            out = out.replace(pat, "[REDACTED]")
    for rx in SECRET_REGEXES:
        out = rx.sub("[REDACTED]", out)
    return out


def guard_output(zone: Zone) -> Callable:
    """Decorador para funciones que retornan strings hacia una zona dada.

    Ejemplo:
        @guard_output(Zone.PUBLIC)
        def render_error(exc): return str(exc)
    """
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            if isinstance(result, str):
                assert_safe(result, zone, label=fn.__name__)
            return result
        return wrapper
    return deco
