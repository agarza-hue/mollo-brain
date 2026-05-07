"""Costs router — resumen de tokens y ahorro vs Claude baseline."""
from fastapi import APIRouter
import cost_service

router = APIRouter(prefix="/costs", tags=["Costs"])


@router.get("/summary")
def summary():
    return {
        "lifetime":    cost_service.lifetime_totals(),
        "by_model":    cost_service.by_model(),
        "last_7_days": cost_service.daily_summary(7),
    }


@router.get("/lifetime")
def lifetime():
    return cost_service.lifetime_totals()


@router.get("/daily")
def daily(days: int = 30):
    return cost_service.daily_summary(days)


@router.get("/by_model")
def by_model():
    return cost_service.by_model()


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
