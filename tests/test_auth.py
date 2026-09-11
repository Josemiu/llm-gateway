from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.middleware.auth import verify_api_key
from app.providers.base import ProviderResponse
from app.providers.gemini_provider import GeminiProvider

REQUEST_BODY = {
    "model": "auto",
    "messages": [{"role": "user", "content": "Hi there"}],
}


@pytest.mark.asyncio
async def test_verify_api_key_missing_header_raises_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.middleware.auth.settings.valid_api_keys", "key1,key2")
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(x_api_key=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_invalid_key_raises_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.middleware.auth.settings.valid_api_keys", "key1,key2")
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(x_api_key="not-a-valid-key")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_without_api_key_returns_401() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json=REQUEST_BODY)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_with_valid_api_key_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.middleware.auth.settings.valid_api_keys", "test-key")
    monkeypatch.setattr(
        "app.middleware.rate_limit.redis_client", fakeredis.FakeAsyncRedis()
    )

    with patch.object(
        GeminiProvider,
        "generate",
        AsyncMock(
            return_value=ProviderResponse(
                content="hello", model="gemini-3.5-flash-lite", input_tokens=1, output_tokens=1
            )
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json=REQUEST_BODY,
                headers={"X-API-Key": "test-key"},
            )

    assert response.status_code == 200
    assert response.json()["content"] == "hello"
