from datetime import timedelta
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from freezegun import freeze_time
from httpx import ASGITransport, AsyncClient, Response

from app.main import app
from app.providers.base import ProviderResponse
from app.providers.gemini_provider import GeminiProvider

REQUEST_BODY = {
    "model": "auto",
    "messages": [{"role": "user", "content": "Hi there"}],
}

TEST_API_KEY = "rate-limit-test-key"


@pytest.fixture(autouse=True)
def _configure_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.middleware.auth.settings.valid_api_keys", TEST_API_KEY)
    monkeypatch.setattr("app.middleware.rate_limit.settings.rate_limit_per_minute", 2)
    monkeypatch.setattr(
        "app.middleware.rate_limit.redis_client", fakeredis.FakeAsyncRedis()
    )


async def _post() -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/v1/chat/completions",
            json=REQUEST_BODY,
            headers={"X-API-Key": TEST_API_KEY},
        )


@pytest.mark.asyncio
async def test_requests_beyond_limit_return_429_with_retry_after() -> None:
    with patch.object(
        GeminiProvider,
        "generate",
        AsyncMock(
            return_value=ProviderResponse(
                content="hi", model="gemini-3.5-flash-lite", input_tokens=1, output_tokens=1
            )
        ),
    ):
        first = await _post()
        second = await _post()
        third = await _post()

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "Retry-After" in third.headers
    assert int(third.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_limit_resets_after_window_expires() -> None:
    with patch.object(
        GeminiProvider,
        "generate",
        AsyncMock(
            return_value=ProviderResponse(
                content="hi", model="gemini-3.5-flash-lite", input_tokens=1, output_tokens=1
            )
        ),
    ):
        with freeze_time("2026-01-01 00:00:00", real_asyncio=True) as frozen:
            await _post()
            await _post()
            blocked = await _post()
            assert blocked.status_code == 429

            frozen.tick(delta=timedelta(seconds=61))

            after_reset = await _post()

    assert after_reset.status_code == 200
