import logging

from sqlalchemy import case, func, select

from app.schemas.usage import UsageSummary
from app.services import database
from app.services.models import UsageRecord
from app.services.pricing import estimate_cost

logger = logging.getLogger(__name__)


async def record_usage(
    *,
    api_key: str,
    model_requested: str,
    model_used: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    status: str,
    used_fallback: bool,
    is_final_attempt: bool = True,
) -> None:
    estimated_cost = estimate_cost(model_used, input_tokens, output_tokens)
    try:
        async with database.async_session_factory() as session:
            session.add(
                UsageRecord(
                    api_key=api_key,
                    model_requested=model_requested,
                    model_used=model_used,
                    provider=provider,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost_usd=estimated_cost,
                    latency_ms=latency_ms,
                    status=status,
                    used_fallback=used_fallback,
                    is_final_attempt=is_final_attempt,
                )
            )
            await session.commit()
    except Exception:
        # This runs as a background task after the response is already on
        # its way to the client - a DB failure here must never surface to
        # the caller, only get logged.
        logger.exception("Failed to record usage for api_key=%s", api_key)


async def get_usage_summary(api_key: str) -> UsageSummary:
    async with database.async_session_factory() as session:
        result = await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0.0),
                func.coalesce(func.avg(UsageRecord.latency_ms), 0.0),
                func.coalesce(
                    func.sum(case((UsageRecord.used_fallback.is_(True), 1), else_=0)),
                    0,
                ),
            ).where(
                UsageRecord.api_key == api_key,
                UsageRecord.is_final_attempt.is_(True),
            )
        )
        (
            total_requests,
            total_input_tokens,
            total_output_tokens,
            total_estimated_cost_usd,
            avg_latency_ms,
            fallback_count,
        ) = result.one()

    fallback_rate = (fallback_count / total_requests) if total_requests else 0.0
    return UsageSummary(
        api_key=api_key,
        total_requests=total_requests,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_estimated_cost_usd=round(total_estimated_cost_usd, 6),
        avg_latency_ms=round(avg_latency_ms, 1),
        fallback_rate=round(fallback_rate, 4),
    )
