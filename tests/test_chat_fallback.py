from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.providers.base import ProviderResponse
from app.providers.gemini_provider import GeminiProvider, GeminiProviderError
from app.providers.openai_provider import OpenAIProvider, OpenAIProviderError

SHORT_PROMPT_BODY = {
    "model": "auto",
    "messages": [{"role": "user", "content": "Hi there"}],
}

TEST_API_KEY = "chat-fallback-test-key"


@pytest.fixture(autouse=True)
def _authorize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.middleware.auth.settings.valid_api_keys", TEST_API_KEY)
    monkeypatch.setattr(
        "app.middleware.rate_limit.redis_client", fakeredis.FakeAsyncRedis()
    )


def _set_dummy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.providers.gemini_provider.settings.gemini_api_key", "gemini-key")
    monkeypatch.setattr("app.providers.openai_provider.settings.openai_api_key", "openai-key")


@pytest.mark.asyncio
async def test_fallback_to_second_provider_on_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_dummy_keys(monkeypatch)

    with (
        patch.object(
            GeminiProvider,
            "generate",
            AsyncMock(side_effect=GeminiProviderError("Gemini down", 503)),
        ),
        patch.object(
            OpenAIProvider,
            "generate",
            AsyncMock(
                return_value=ProviderResponse(
                    content="fallback answer",
                    model="gpt-4o-mini",
                    input_tokens=3,
                    output_tokens=2,
                )
            ),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json=SHORT_PROMPT_BODY,
                headers={"X-API-Key": TEST_API_KEY},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "fallback answer"
    assert body["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_both_providers_failing_returns_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_dummy_keys(monkeypatch)

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
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json=SHORT_PROMPT_BODY,
                headers={"X-API-Key": TEST_API_KEY},
            )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "gemini" in detail
    assert "openai" in detail
    assert "Gemini down" in detail
    assert "OpenAI down" in detail


@pytest.mark.asyncio
async def test_unrecognized_model_returns_400_without_calling_any_provider() -> None:
    with (
        patch.object(GeminiProvider, "generate", AsyncMock()) as gemini_generate,
        patch.object(OpenAIProvider, "generate", AsyncMock()) as openai_generate,
    ):
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
    gemini_generate.assert_not_called()
    openai_generate.assert_not_called()
