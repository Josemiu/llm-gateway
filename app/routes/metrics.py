from fastapi import APIRouter

from app.schemas.metrics import MetricsSummary
from app.services.metrics_service import get_global_metrics

router = APIRouter()


@router.get("/metrics", response_model=MetricsSummary)
async def get_metrics() -> MetricsSummary:
    return await get_global_metrics()
