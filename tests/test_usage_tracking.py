from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.providers.base import ProviderResponse
from app.providers.gemini_provider import GeminiProvider, GeminiProviderError
from app.providers.openai_provider import OpenAIProvider, OpenAIProviderError
from app.services.models import UsageRecord

TEST_API_KEY = "usage-tracking-test-key"

SHORT_PROMPT_BODY = {
    "model": "auto",
    "messages": [{"role": "user", "content": "Hi there"}],
}


@pytest.fixture(autouse=True)
def _authorize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.middleware.auth.settings.valid_api_keys", TEST_API_KEY)
    monkeypatch.setattr(
        "app.middleware.rate_limit.redis_client", fakeredis.FakeAsyncRedis()
    )


async def _post() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/v1/chat/completions",
            json=SHORT_PROMPT_BODY,
            headers={"X-API-Key": TEST_API_KEY},
        )


@pytest.mark.asyncio
async def test_successful_request_creates_success_record(usage_db) -> None:
    with patch.object(
        GeminiProvider,
        "generate",
        AsyncMock(
            return_value=ProviderResponse(
                content="hi", model="gemini-3.5-flash-lite", input_tokens=10, output_tokens=5
            )
        ),
    ):
        response = await _post()

    assert response.status_code == 200

    async with usage_db() as session:
        records = (await session.execute(select(UsageRecord))).scalars().all()

    assert len(records) == 1
    record = records[0]
    assert record.api_key == TEST_API_KEY
    assert record.status == "success"
    assert record.used_fallback is False
    assert record.provider == "gemini"
    assert record.model_used == "gemini-3.5-flash-lite"
    assert record.input_tokens == 10
    assert record.output_tokens == 5
    assert record.estimated_cost_usd > 0
    assert record.latency_ms >= 0


@pytest.mark.asyncio
async def test_both_providers_failing_creates_error_record(usage_db) -> None:
    with (
        patch.object(
            GeminiProvider,
            "generate",
            AsyncMock(side_effect=GeminiProviderError("Gemini down", 503)),
        ),
        patch.object(
            OpenAIProvider,
            "generate",
            AsyncMock(side_effect=OpenAIProviderError("OpenAI down", 502)),
        ),
    ):
        response = await _post()

    assert response.status_code == 502

    async with usage_db() as session:
        records = (await session.execute(select(UsageRecord))).scalars().all()

    assert len(records) == 1
    record = records[0]
    assert record.status == "error"
    assert record.used_fallback is True
    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.estimated_cost_usd == 0.0


@pytest.mark.asyncio
async def test_unrecognized_model_creates_no_record(usage_db) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-3-opus",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"X-API-Key": TEST_API_KEY},
        )

    assert response.status_code == 400

    async with usage_db() as session:
        records = (await session.execute(select(UsageRecord))).scalars().all()

    assert len(records) == 0
