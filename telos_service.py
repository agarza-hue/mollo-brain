"""
TELOS — Compressed user identity & goals layer, port from PAI.

Mantiene los archivos en ./telos/ y compone PRINCIPAL_TELOS.md como vista
resumen que claude_service inyecta en el system prompt en cada request.

Files maestros (editables):
    MISSION.md, GOALS.md, PROBLEMS.md, STRATEGIES.md, NARRATIVES.md, CHALLENGES.md

Files derivados (NO editar a mano):
    PRINCIPAL_TELOS.md — regenerado por regenerate_summary()
"""
from pathlib import Path
from datetime import datetime

TELOS_DIR = Path(__file__).parent / "telos"
SOURCES = ["MISSION", "GOALS", "PROBLEMS", "STRATEGIES", "NARRATIVES", "CHALLENGES"]
PRINCIPAL = TELOS_DIR / "PRINCIPAL_TELOS.md"


def _read(name: str) -> str:
    p = TELOS_DIR / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _write(name: str, content: str) -> None:
    p = TELOS_DIR / f"{name}.md"
    p.write_text(content, encoding="utf-8")


def _extract_bullets(md: str) -> list[str]:
    """Pulla bullets reales (- texto, * texto) y descarta:
       - Horizontal rules (---, ***)
       - Placeholders italics (_(...)_, _(pendiente)_)
       - Notas-meta después de un '---' (todo lo posterior a un HR se ignora)
    'pendiente' como palabra dentro de un bullet legítimo NO descalifica."""
    out: list[str] = []
    seen_hr = False
    for line in md.splitlines():
        s = line.strip()
        if not s:
            continue
        # Horizontal rule: --- o ***. Detener extracción.
        if s in ("---", "***") or (set(s) <= {"-"} and len(s) >= 3) or (set(s) <= {"*"} and len(s) >= 3):
            seen_hr = True
            continue
        if seen_hr:
            continue
        if s.startswith(("- ", "* ")):
            text = s[2:].strip()
            # Skip placeholders italics: _(pendiente)_, _(define con...)_, etc.
            if text.startswith("_(") and text.endswith(")_"):
                continue
            if text:
                out.append(text)
    return out


def load_principal_telos() -> str:
    """Devuelve el contenido actual de PRINCIPAL_TELOS.md. Usado por claude_service
    al construir el system prompt."""
    if not PRINCIPAL.exists():
        return ""
    return PRINCIPAL.read_text(encoding="utf-8")


def regenerate_summary() -> dict:
    """Recompone PRINCIPAL_TELOS.md desde los archivos fuente. Solo extrae bullets
    no-placeholder; secciones vacías quedan como '_Sin definir._'."""
    sections = {name: _extract_bullets(_read(name)) for name in SOURCES}
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    def fmt(name: str) -> str:
        items = sections[name]
        if not items:
            return f"- _Sin definir._ Edita `telos/{name}.md` y regenera.\n"
        return "\n".join(f"- {b}" for b in items) + "\n"

    body = (
        "# Principal TELOS — Adolfo\n\n"
        f"> Regenerado: {ts}. Auto-generado por `telos_service.regenerate_summary()`. NO editar a mano.\n\n"
        "## Misiones\n\n" + fmt("MISSION") + "\n"
        "## Goals activos\n\n" + fmt("GOALS") + "\n"
        "## Problemas que resuelvo\n\n" + fmt("PROBLEMS") + "\n"
        "## Estrategias\n\n" + fmt("STRATEGIES") + "\n"
        "## Narrativas activas\n\n" + fmt("NARRATIVES") + "\n"
        "## Challenges personales\n\n" + fmt("CHALLENGES") + "\n"
        "---\n*Este archivo es la vista comprimida del usuario. Mollo lo usa para "
        "priorizar, sugerir, y mantener alineadas las respuestas con los goals reales.*\n"
    )
    PRINCIPAL.write_text(body, encoding="utf-8")
    return {"sections": {k: len(v) for k, v in sections.items()}, "regenerated_at": ts}


def get_source(name: str) -> str:
    """Devuelve el contenido raw de un archivo fuente."""
    if name not in SOURCES:
        raise ValueError(f"Source desconocido: {name}. Válidos: {SOURCES}")
    return _read(name)


def update_source(name: str, content: str) -> dict:
    """Sobreescribe un archivo fuente y regenera el principal."""
    if name not in SOURCES:
        raise ValueError(f"Source desconocido: {name}. Válidos: {SOURCES}")
    _write(name, content)
    return regenerate_summary()
