"""Costs router — resumen de tokens y ahorro vs Claude baseline."""
from fastapi import APIRouter
import cost_service

router = APIRouter(prefix="/costs", tags=["Costs"])


@router.get("/summary")
def summary(exclude_modos: str | None = None):
    return {
        "lifetime":    cost_service.lifetime_totals(exclude_modos=exclude_modos),
        "by_model":    cost_service.by_model(exclude_modos=exclude_modos),
        "last_7_days": cost_service.daily_summary(7, exclude_modos=exclude_modos),
    }


@router.get("/lifetime")
def lifetime(exclude_modos: str | None = None):
    return cost_service.lifetime_totals(exclude_modos=exclude_modos)


@router.get("/daily")
def daily(days: int = 30, exclude_modos: str | None = None):
    return cost_service.daily_summary(days, exclude_modos=exclude_modos)


@router.get("/by_model")
def by_model(exclude_modos: str | None = None):
    return cost_service.by_model(exclude_modos=exclude_modos)


@router.get("/recent")
def recent(limit: int = 20):
    return cost_service.recent(limit)


@router.get("/by_topic")
def by_topic():
    return cost_service.by_topic()


@router.get("/by_provider")
def by_provider():
    return cost_service.by_provider()


@router.get("/topic_by_model")
def topic_by_model():
    return cost_service.topic_by_model()


@router.get("/by_tenant")
def by_tenant():
    return cost_service.by_tenant()


@router.get("/by_tenant_model")
def by_tenant_model():
    return cost_service.by_tenant_model()


@router.get("/top_queries")
def top_queries(limit: int = 5, days: int | None = None):
    return cost_service.top_queries(limit=limit, days=days)


@router.get("/weekly")
def weekly():
    return cost_service.weekly_comparison()


@router.get("/range")
def range_summary(start: str, end: str):
    return cost_service.range_summary(start, end)


@router.get("/roi")
def infrastructure_roi(days: int | None = None):
    return cost_service.infrastructure_roi(days=days)
