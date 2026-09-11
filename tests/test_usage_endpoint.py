import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.models import UsageRecord

TEST_API_KEY = "usage-endpoint-test-key"
OTHER_API_KEY = "someone-elses-key"


@pytest.fixture(autouse=True)
def _authorize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.middleware.auth.settings.valid_api_keys", f"{TEST_API_KEY},{OTHER_API_KEY}"
    )


async def _seed(session_factory, records: list[UsageRecord]) -> None:
    async with session_factory() as session:
        for record in records:
            session.add(record)
        await session.commit()


async def _get_usage(api_key: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/v1/usage", headers={"X-API-Key": api_key})


@pytest.mark.asyncio
async def test_usage_summary_aggregates_records_for_the_given_key(usage_db) -> None:
    await _seed(
        usage_db,
        [
            UsageRecord(
                api_key=TEST_API_KEY,
                model_requested="auto",
                model_used="gemini-3.5-flash-lite",
                provider="gemini",
                input_tokens=100,
                output_tokens=50,
                estimated_cost_usd=0.01,
                latency_ms=200,
                status="success",
                used_fallback=False,
            ),
            UsageRecord(
                api_key=TEST_API_KEY,
                model_requested="auto",
                model_used="gpt-4o-mini",
                provider="openai",
                input_tokens=200,
                output_tokens=80,
                estimated_cost_usd=0.02,
                latency_ms=400,
                status="success",
                used_fallback=True,
            ),
            UsageRecord(
                api_key=TEST_API_KEY,
                model_requested="auto",
                model_used="gpt-4o-mini",
                provider="openai",
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=0.0,
                latency_ms=600,
                status="error",
                used_fallback=True,
            ),
            # Belongs to a different key - must not be counted.
            UsageRecord(
                api_key=OTHER_API_KEY,
                model_requested="auto",
                model_used="gemini-3.5-flash-lite",
                provider="gemini",
                input_tokens=9999,
                output_tokens=9999,
                estimated_cost_usd=99.0,
                latency_ms=9999,
                status="success",
                used_fallback=False,
            ),
        ],
    )

    response = await _get_usage(TEST_API_KEY)

    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] == TEST_API_KEY
    assert body["total_requests"] == 3
    assert body["total_input_tokens"] == 300
    assert body["total_output_tokens"] == 130
    assert body["total_estimated_cost_usd"] == pytest.approx(0.03)
    assert body["avg_latency_ms"] == pytest.approx(400.0)
    assert body["fallback_rate"] == pytest.approx(2 / 3, abs=1e-4)


@pytest.mark.asyncio
async def test_usage_summary_for_key_with_no_records_is_all_zero(usage_db) -> None:
    response = await _get_usage(TEST_API_KEY)

    assert response.status_code == 200
    body = response.json()
    assert body["total_requests"] == 0
    assert body["total_input_tokens"] == 0
    assert body["total_output_tokens"] == 0
    assert body["total_estimated_cost_usd"] == 0.0
    assert body["avg_latency_ms"] == 0.0
    assert body["fallback_rate"] == 0.0


@pytest.mark.asyncio
async def test_usage_endpoint_requires_valid_api_key(usage_db) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/usage")

    assert response.status_code == 401
