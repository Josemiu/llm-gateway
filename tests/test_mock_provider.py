import pytest

from app.config import settings
from app.providers.base import ProviderError
from app.providers.mock_provider import MockProvider
from app.schemas.chat import ChatMessage


@pytest.fixture(autouse=True)
def fast_mock_latency(monkeypatch: pytest.MonkeyPatch):
    """Keep these tests fast: sleep 0-1ms instead of the real load-test range."""
    monkeypatch.setattr(settings, "mock_provider_latency_ms_min", 0)
    monkeypatch.setattr(settings, "mock_provider_latency_ms_max", 1)


@pytest.mark.asyncio
async def test_generate_returns_plausible_usage() -> None:
    provider = MockProvider("gemini")
    messages = [ChatMessage(role="user", content="one two three four five")]

    result = await provider.generate(model="gemini-3.5-flash-lite", messages=messages)

    assert result.model == "gemini-3.5-flash-lite"
    assert result.input_tokens == 5
    assert result.output_tokens > 0


@pytest.mark.asyncio
async def test_generate_raises_when_provider_is_in_forced_failure_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mock_provider_fail", "openai,gemini")
    provider = MockProvider("openai")

    with pytest.raises(ProviderError):
        await provider.generate(
            model="gpt-4o-mini", messages=[ChatMessage(role="user", content="hi")]
        )


@pytest.mark.asyncio
async def test_generate_only_fails_for_providers_in_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mock_provider_fail", "openai")
    provider = MockProvider("gemini")

    result = await provider.generate(
        model="gemini-3.5-flash-lite", messages=[ChatMessage(role="user", content="hi")]
    )

    assert result.model == "gemini-3.5-flash-lite"
