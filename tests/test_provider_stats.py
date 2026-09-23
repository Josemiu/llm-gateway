from datetime import datetime, timedelta, timezone

import pytest

from app.services.models import UsageRecord
from app.services.provider_stats_service import get_provider_stats

NOW = datetime.now(timezone.utc)


def _record(
    *,
    provider: str,
    status: str,
    latency_ms: int,
    created_at: datetime,
    is_final_attempt: bool = True,
) -> UsageRecord:
    return UsageRecord(
        api_key="whoever",
        model_requested="auto",
        model_used="gemini-3.5-flash-lite" if provider == "gemini" else "gpt-4o-mini",
        provider=provider,
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.0,
        latency_ms=latency_ms,
        status=status,
        used_fallback=not is_final_attempt,
        is_final_attempt=is_final_attempt,
        created_at=created_at,
    )


async def _seed(session_factory, records: list[UsageRecord]) -> None:
    async with session_factory() as session:
        for record in records:
            session.add(record)
        await session.commit()


@pytest.mark.asyncio
async def test_counts_include_shadowed_primary_failures(usage_db) -> None:
    await _seed(
        usage_db,
        [
            _record(provider="gemini", status="success", latency_ms=200, created_at=NOW),
            # Shadow row: gemini failed as primary, openai succeeded as
            # fallback. Must still count toward gemini's error rate even
            # though /v1/usage and /metrics would skip it.
            _record(
                provider="gemini",
                status="error",
                latency_ms=50,
                created_at=NOW,
                is_final_attempt=False,
            ),
        ],
    )

    stats = await get_provider_stats(window_minutes=60)

    assert stats["gemini"].sample_count == 2
    assert stats["gemini"].error_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_avg_latency_only_considers_successful_attempts(usage_db) -> None:
    await _seed(
        usage_db,
        [
            _record(provider="openai", status="success", latency_ms=100, created_at=NOW),
            _record(provider="openai", status="success", latency_ms=300, created_at=NOW),
            # A fast failure would pull the average down if counted here.
            _record(provider="openai", status="error", latency_ms=5, created_at=NOW),
        ],
    )

    stats = await get_provider_stats(window_minutes=60)

    assert stats["openai"].avg_latency_ms == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_records_outside_the_window_are_excluded(usage_db) -> None:
    await _seed(
        usage_db,
        [
            _record(provider="gemini", status="success", latency_ms=100, created_at=NOW),
            _record(
                provider="gemini",
                status="error",
                latency_ms=100,
                created_at=NOW - timedelta(minutes=90),
            ),
        ],
    )

    stats = await get_provider_stats(window_minutes=60)

    assert stats["gemini"].sample_count == 1
    assert stats["gemini"].error_rate == 0.0


@pytest.mark.asyncio
async def test_provider_with_no_records_in_window_is_absent(usage_db) -> None:
    stats = await get_provider_stats(window_minutes=60)

    assert stats == {}
