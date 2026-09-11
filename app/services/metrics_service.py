from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.metrics import MetricsSummary, MetricsWindow, TodayMetricsWindow
from app.services import database
from app.services.models import UsageRecord

LAST_WINDOW_SECONDS = 60


async def _aggregate_since(
    session: AsyncSession, cutoff: datetime
) -> tuple[int, float, int, int, float]:
    result = await session.execute(
        select(
            func.count(),
            func.coalesce(func.avg(UsageRecord.latency_ms), 0.0),
            func.coalesce(
                func.sum(case((UsageRecord.status == "error", 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((UsageRecord.used_fallback.is_(True), 1), else_=0)), 0
            ),
            func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0.0),
        ).where(UsageRecord.created_at >= cutoff)
    )
    return result.one()


def _build_window(
    count: int,
    avg_latency: float,
    error_count: int,
    fallback_count: int,
    elapsed_seconds: float,
) -> dict:
    return {
        "request_count": count,
        "requests_per_second": count / elapsed_seconds if elapsed_seconds > 0 else 0.0,
        "avg_latency_ms": round(avg_latency, 1),
        "error_rate": round(error_count / count, 4) if count else 0.0,
        "fallback_rate": round(fallback_count / count, 4) if count else 0.0,
    }


async def get_global_metrics() -> MetricsSummary:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with database.async_session_factory() as session:
        last_60s_row = await _aggregate_since(
            session, now - timedelta(seconds=LAST_WINDOW_SECONDS)
        )
        today_row = await _aggregate_since(session, today_start)

    last_60s = _build_window(*last_60s_row[:4], elapsed_seconds=LAST_WINDOW_SECONDS)

    today_elapsed = max((now - today_start).total_seconds(), 1.0)
    today = _build_window(*today_row[:4], elapsed_seconds=today_elapsed)
    today["cost_usd"] = round(today_row[4], 6)

    return MetricsSummary(
        last_60s=MetricsWindow(**last_60s),
        today=TodayMetricsWindow(**today),
    )
