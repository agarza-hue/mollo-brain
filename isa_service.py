"""
ISA — Ideal State Artifact, port del pack PAI/ISA.

Documento universal de "done" para cualquier tarea (proyecto, app, infra,
trabajo creativo). 12 secciones en orden fijo. Es como un PRD pero genérico
— sirve tanto para "construir endpoint X" como "escribir mi blog post Y".

Workflows:
    scaffold(task, tier)        — genera ISA fresca desde un prompt
    check_completeness(isa,t)   — score contra tier gate, devuelve gaps
    save(isa_id, content)       — persiste ISA al disco

Tiers (completeness gates):
    E1 — Goal + Criteria mínimos (tarea pequeña)
    E2 — + Problem + Vision
    E3 — + Out-of-Scope + Principles + Constraints
    E4 — + Test Strategy + Features
    E5 — todas las 12 secciones (pre-BUILD interview completa)
"""
from pathlib import Path
from datetime import datetime
import json
import re

ISA_DIR = Path(__file__).parent / "isas"
ISA_DIR.mkdir(exist_ok=True)

SECTIONS = [
    "Problem",        # 1 — Qué está roto o falta ahora
    "Vision",         # 2 — El outcome eufórico (1-5 oraciones)
    "Out of Scope",   # 3 — Lo explícitamente excluido
    "Principles",     # 4 — Verdades substrate-independent que el trabajo respeta
    "Constraints",    # 5 — Boundaries arquitectónicas inmovibles
    "Goal",           # 6 — La columna hard-to-vary del done verificable (1-3 oraciones)
    "Criteria",       # 7 — ISCs atómicos binary-testable + anti-criterios
    "Test Strategy",  # 8 — Verificación por ISC + thresholds
    "Features",       # 9 — Breakdown con deps + paralelización
    "Decisions",      # 10 — Log timestamped (incluyendo dead ends)
    "Changelog",      # 11 — Error-correction trail
    "Verification",   # 12 — Evidencia de que cada criterio pasó
]

TIER_GATES = {
    1: ["Goal", "Criteria"],
    2: ["Problem", "Vision", "Goal", "Criteria"],
    3: ["Problem", "Vision", "Out of Scope", "Principles", "Constraints", "Goal", "Criteria"],
    4: ["Problem", "Vision", "Out of Scope", "Principles", "Constraints", "Goal", "Criteria",
        "Test Strategy", "Features"],
    5: SECTIONS,
}


def _slug(s: str, n: int = 50) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower())[:n].strip("-")
    return s or "isa"


def scaffold(task: str, tier: int = 2) -> str:
    """Genera markdown skeleton de ISA con todas las secciones, marcando
    cuáles son requeridas por el tier dado. NO llama a un LLM — solo
    devuelve el template. Para auto-fill llama a interview() después."""
    required = set(TIER_GATES.get(tier, TIER_GATES[5]))
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    out = [
        f"# ISA — {task}",
        "",
        f"> Tier E{tier} · creado {ts}",
        "",
        "## Metadata",
        "",
        f"- **Task:** {task}",
        f"- **Tier:** E{tier} ({', '.join(TIER_GATES[tier])} mínimos)",
        f"- **Created:** {ts}",
        "- **Status:** scaffolded — needs interview",
        "",
    ]
    for sec in SECTIONS:
        mark = "🎯" if sec in required else "○"
        out.append(f"## {mark} {sec}")
        out.append("")
        if sec in required:
            out.append(f"_(REQUERIDO para tier E{tier} — define antes de BUILD)_")
        else:
            out.append("_(opcional en este tier)_")
        out.append("")
    return "\n".join(out)


def check_completeness(isa_md: str, tier: int = 2) -> dict:
    """Verifica qué secciones requeridas están vacías. Devuelve gaps + score."""
    required = TIER_GATES.get(tier, TIER_GATES[5])
    gaps: list[str] = []
    filled: list[str] = []
    for sec in required:
        # busca el header — tolera numeración (## 1. Problem), tier marker (🎯/○),
        # y suffix descriptivo (## 7. Criteria (ISCs medibles)). Hasta el siguiente '##'.
        pattern = re.compile(
            rf"##\s*(?:\d+\.\s*)?[🎯○]?\s*{re.escape(sec)}\b[^\n]*\n(.*?)(?=\n##\s|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        m = pattern.search(isa_md)
        if not m:
            gaps.append(sec)
            continue
        body = m.group(1).strip()
        # Vacío si solo placeholders
        if (not body) or all(line.strip().startswith("_(") or not line.strip()
                              for line in body.splitlines()):
            gaps.append(sec)
        else:
            filled.append(sec)
    score = round(100 * len(filled) / len(required)) if required else 0
    return {
        "tier": tier,
        "required_sections": required,
        "filled": filled,
        "gaps": gaps,
        "score": score,
        "passes_gate": len(gaps) == 0,
    }


def save(isa_id: str, content: str) -> Path:
    """Persiste ISA a ./isas/<slug>.md. Devuelve el path."""
    p = ISA_DIR / f"{_slug(isa_id)}.md"
    p.write_text(content, encoding="utf-8")
    return p


def load(isa_id: str) -> str | None:
    """Lee ISA por slug. Devuelve None si no existe."""
    p = ISA_DIR / f"{_slug(isa_id)}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def list_isas() -> list[dict]:
    """Lista ISAs guardadas con metadatos básicos."""
    out: list[dict] = []
    for p in sorted(ISA_DIR.glob("*.md")):
        stat = p.stat()
        # Pulla el título de la primera línea
        first = p.read_text(encoding="utf-8").splitlines()[0] if p.stat().st_size else ""
        title = first.lstrip("# ").strip() if first.startswith("#") else p.stem
        out.append({
            "id": p.stem,
            "title": title,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "size": stat.st_size,
        })
    return out
