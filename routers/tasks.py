"""Tasks router — Mollo Task Engine endpoints."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

from task_engine import (
    TaskDefinition, TaskRunRequest, TaskResult,
    TaskEngine, register_task, get_task_definition,
    list_task_definitions, get_run, list_runs,
)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class RegisterRequest(BaseModel):
    definition: dict[str, Any]


@router.post("/register", summary="Registrar definición de task")
def register(req: RegisterRequest):
    try:
        task = TaskDefinition.model_validate(req.definition)
    except Exception as e:
        raise HTTPException(422, str(e))
    register_task(task)
    return {"status": "ok", "task_id": task.task_id}


@router.get("/", summary="Listar tasks registradas")
def list_tasks():
    return list_task_definitions()


@router.get("/{task_id}", summary="Obtener definición de task")
def get_task(task_id: str):
    t = get_task_definition(task_id)
    if not t:
        raise HTTPException(404, f"Task '{task_id}' not found")
    return t.model_dump()


@router.post("/run", summary="Ejecutar task")
async def run_task(req: TaskRunRequest):
    task = get_task_definition(req.task_id)
    if not task:
        raise HTTPException(404, f"Task '{req.task_id}' not found")
    result = await TaskEngine.execute(task, req.context, dry_run=req.dry_run)
    return result.model_dump()


@router.post("/dry", summary="Dry-run de task (sin efectos reales)")
async def dry_run_task(req: TaskRunRequest):
    task = get_task_definition(req.task_id)
    if not task:
        raise HTTPException(404, f"Task '{req.task_id}' not found")
    result = await TaskEngine.execute(task, req.context, dry_run=True)
    return result.model_dump()


@router.post("/run_inline", summary="Ejecutar task desde definición (sin registrar)")
async def run_inline(body: dict[str, Any], dry: bool = False):
    try:
        task = TaskDefinition.model_validate(body)
    except Exception as e:
        raise HTTPException(422, str(e))
    result = await TaskEngine.execute(task, {}, dry_run=dry)
    return result.model_dump()


@router.get("/runs/{run_id}", summary="Estado de un run")
def get_run_status(run_id: str):
    r = get_run(run_id)
    if not r:
        raise HTTPException(404, f"Run '{run_id}' not found")
    return r


@router.get("/runs/list/{task_id}", summary="Historial de runs de una task")
def list_task_runs(task_id: str, limit: int = 20):
    return list_runs(task_id, limit)


@router.get("/runs/all/recent", summary="Runs recientes de todas las tasks")
def list_recent_runs(limit: int = 20):
    return list_runs(limit=limit)
