from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from app.services import database
from app.services.models import UsageRecord
from app.telemetry import tracer


@dataclass(frozen=True)
class ProviderStats:
    provider: str
    sample_count: int
    error_rate: float
    avg_latency_ms: float


async def get_provider_stats(window_minutes: int) -> dict[str, ProviderStats]:
    """Real per-provider reliability/latency stats over the last
    `window_minutes`, from every attempt recorded (including a primary
    provider's failed attempts that got superseded by a fallback - see
    DECISIONS.md and app/services/models.py - unlike /v1/usage and /metrics,
    which intentionally only count the attempt whose outcome reached the
    client). avg_latency_ms is computed over successful attempts only: a
    fast failure would otherwise pull the average down and make a provider
    look faster than it actually is when it works.
    """
    with tracer.start_as_current_span("db.get_provider_stats") as span:
        span.set_attribute("window_minutes", window_minutes)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

        async with database.async_session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        UsageRecord.provider,
                        func.count(),
                        func.coalesce(
                            func.sum(case((UsageRecord.status == "error", 1), else_=0)), 0
                        ),
                        func.coalesce(
                            func.avg(
                                case(
                                    (UsageRecord.status == "success", UsageRecord.latency_ms)
                                )
                            ),
                            0.0,
                        ),
                    )
                    .where(UsageRecord.created_at >= cutoff)
                    .group_by(UsageRecord.provider)
                )
            ).all()

        return {
            provider: ProviderStats(
                provider=provider,
                sample_count=count,
                error_rate=round(error_count / count, 4) if count else 0.0,
                avg_latency_ms=round(avg_latency, 1),
            )
            for provider, count, error_count, avg_latency in rows
        }
