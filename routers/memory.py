"""Endpoints para gestión de memoria de Mollo."""
from fastapi import APIRouter
from pydantic import BaseModel
from memory_service import get_all_memory, update_business_context, save_learning

router = APIRouter(prefix="/memory", tags=["Memoria"])


class BusinessContextUpdate(BaseModel):
    clave: str
    valor: str


class LearningEntry(BaseModel):
    tema: str
    insight: str


@router.get("/")
def get_memory():
    return get_all_memory()


@router.post("/business")
def set_business_context(req: BusinessContextUpdate):
    update_business_context(req.clave, req.valor)
    return {"status": "ok", "guardado": req.clave}


@router.post("/learning")
def add_learning(req: LearningEntry):
    save_learning(req.tema, req.insight)
    return {"status": "ok"}
