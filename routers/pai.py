"""
PAI router — endpoints para Telos, ISA, ContainmentGuard.

Tres conceptos robados del repo PAI (danielmiessler/Personal_AI_Infrastructure)
y portados a Mollo:
- /pai/telos/*   — identity & goals layer
- /pai/isa/*     — Ideal State Artifact (PRD universal)
- /pai/guard/*   — Containment scan (privacy by code)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import telos_service
import isa_service
import containment_guard

router = APIRouter(prefix="/pai", tags=["pai"])


# ───────── Telos ─────────

class TelosUpdate(BaseModel):
    name: str        # MISSION | GOALS | PROBLEMS | STRATEGIES | NARRATIVES | CHALLENGES
    content: str


@router.get("/telos/principal")
def get_principal_telos():
    """Devuelve PRINCIPAL_TELOS.md (vista resumen que claude_service inyecta)."""
    return {"content": telos_service.load_principal_telos()}


@router.get("/telos/source/{name}")
def get_telos_source(name: str):
    """Lee un archivo fuente (MISSION, GOALS, etc.)."""
    try:
        return {"name": name, "content": telos_service.get_source(name)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/telos/source")
def update_telos_source(body: TelosUpdate):
    """Sobreescribe un archivo fuente y regenera el principal."""
    try:
        result = telos_service.update_source(body.name, body.content)
        return {"ok": True, "regenerated": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/telos/regenerate")
def regenerate_telos():
    """Recompone PRINCIPAL_TELOS.md desde las fuentes."""
    return telos_service.regenerate_summary()


# ───────── ISA ─────────

class ISAScaffold(BaseModel):
    task: str
    tier: int = 2


class ISASave(BaseModel):
    isa_id: str
    content: str


class ISACheck(BaseModel):
    content: str
    tier: int = 2


@router.post("/isa/scaffold")
def scaffold_isa(body: ISAScaffold):
    """Genera ISA skeleton markdown desde un task description."""
    if body.tier < 1 or body.tier > 5:
        raise HTTPException(400, "tier debe estar entre 1 y 5")
    return {"markdown": isa_service.scaffold(body.task, body.tier)}


@router.post("/isa/check")
def check_isa(body: ISACheck):
    """Verifica completeness del ISA contra su tier."""
    return isa_service.check_completeness(body.content, body.tier)


@router.post("/isa/save")
def save_isa(body: ISASave):
    """Persiste una ISA al disco."""
    p = isa_service.save(body.isa_id, body.content)
    return {"saved": str(p), "id": body.isa_id}


@router.get("/isa/{isa_id}")
def get_isa(isa_id: str):
    content = isa_service.load(isa_id)
    if content is None:
        raise HTTPException(404, f"ISA '{isa_id}' no encontrada")
    return {"id": isa_id, "content": content}


@router.get("/isa")
def list_isas():
    return {"isas": isa_service.list_isas()}


# ───────── ContainmentGuard ─────────

class GuardScan(BaseModel):
    content: str
    zone: str = "public"    # public | trusted


@router.post("/guard/scan")
def guard_scan(body: GuardScan):
    """Escanea contenido. Si va a zona PUBLIC y matchea, devuelve violation.
    No raise — solo reporta. Usa /guard/assert si quieres bloquear."""
    hit = containment_guard.find_violation(body.content)
    return {
        "zone": body.zone,
        "violation": hit,
        "safe": hit is None,
    }


@router.post("/guard/redact")
def guard_redact(body: GuardScan):
    """Reemplaza secretos por [REDACTED] sin bloquear. Útil para logs."""
    return {"redacted": containment_guard.redact(body.content)}
