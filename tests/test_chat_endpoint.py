from unittest.mock import AsyncMock, patch

import pytest
from google.genai import types
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.providers.base import LLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider

REQUEST_BODY = {
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain Kubernetes"}],
}


def _fake_response() -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Kubernetes orchestrates containers.")],
                ),
                finish_reason="STOP",
                index=0,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5, total_token_count=15
        ),
    )


@pytest.mark.asyncio
async def test_chat_completion_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.providers.gemini_provider.settings.gemini_api_key", "test-key"
    )

    with patch("app.providers.gemini_provider.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=_fake_response()
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/chat/completions", json=REQUEST_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Kubernetes orchestrates containers."
    assert body["model"] == "gemini-3.5-flash-lite"
    assert body["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


@pytest.mark.asyncio
async def test_chat_completion_all_providers_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.providers.gemini_provider.settings.gemini_api_key", None)
    monkeypatch.setattr("app.providers.openai_provider.settings.openai_api_key", None)

    with (
        patch("app.providers.gemini_provider.genai.Client") as mock_gemini_cls,
        patch("app.providers.openai_provider.AsyncOpenAI") as mock_openai_cls,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/chat/completions", json=REQUEST_BODY)

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "GEMINI_API_KEY" in detail
    assert "OPENAI_API_KEY" in detail
    mock_gemini_cls.assert_not_called()
    mock_openai_cls.assert_not_called()


def test_providers_implement_llm_provider_interface() -> None:
    assert isinstance(GeminiProvider(), LLMProvider)
    assert isinstance(OpenAIProvider(), LLMProvider)
