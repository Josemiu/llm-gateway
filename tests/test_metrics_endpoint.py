from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.models import UsageRecord

UTC_NOW = datetime.now(timezone.utc)


def _record(*, created_at: datetime, status: str, used_fallback: bool, cost: float) -> UsageRecord:
    return UsageRecord(
        api_key="whoever",
        model_requested="auto",
        model_used="gemini-3.5-flash-lite",
        provider="gemini",
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=cost,
        latency_ms=100,
        status=status,
        used_fallback=used_fallback,
        created_at=created_at,
    )


async def _seed(session_factory, records: list[UsageRecord]) -> None:
    async with session_factory() as session:
        for record in records:
            session.add(record)
        await session.commit()


async def _get_metrics():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/metrics")


@pytest.mark.asyncio
async def test_metrics_split_recent_vs_today(usage_db) -> None:
    await _seed(
        usage_db,
        [
            # 2 recent (within last 60s): 1 success, 1 error.
            _record(created_at=UTC_NOW, status="success", used_fallback=False, cost=0.01),
            _record(
                created_at=UTC_NOW - timedelta(seconds=5),
                status="error",
                used_fallback=True,
                cost=0.0,
            ),
            # 1 older (just past the 60s window, still today): success with fallback.
            # Using 65s rather than e.g. 2 hours keeps this safely away from a
            # UTC-midnight day-boundary crossing, which would break the "today"
            # assumption below.
            _record(
                created_at=UTC_NOW - timedelta(seconds=65),
                status="success",
                used_fallback=True,
                cost=0.02,
            ),
        ],
    )

    response = await _get_metrics()

    assert response.status_code == 200
    body = response.json()

    last_60s = body["last_60s"]
    assert last_60s["request_count"] == 2
    assert last_60s["error_rate"] == pytest.approx(0.5)
    assert last_60s["fallback_rate"] == pytest.approx(0.5)

    today = body["today"]
    assert today["request_count"] == 3
    assert today["error_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert today["fallback_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert today["cost_usd"] == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_metrics_with_no_data_is_all_zero(usage_db) -> None:
    response = await _get_metrics()

    assert response.status_code == 200
    body = response.json()

    for window in (body["last_60s"], body["today"]):
        assert window["request_count"] == 0
        assert window["requests_per_second"] == 0.0
        assert window["avg_latency_ms"] == 0.0
        assert window["error_rate"] == 0.0
        assert window["fallback_rate"] == 0.0

    assert body["today"]["cost_usd"] == 0.0
