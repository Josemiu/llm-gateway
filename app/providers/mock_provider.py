import asyncio
import random

from app.config import settings
from app.providers.base import LLMProvider, ProviderError, ProviderResponse
from app.schemas.chat import ChatMessage, Tool


class MockProviderError(ProviderError):
    pass


class MockProvider(LLMProvider):
    """Stand-in for OpenAIProvider/GeminiProvider, used only when
    `settings.load_test_mode` is enabled (see DECISIONS.md). Simulates
    provider latency and returns synthetic token counts instead of calling
    a real LLM API, so k6 load tests exercise the gateway's own overhead
    (auth, rate limiting, routing, fallback, background DB writes) without
    real API cost or being bottlenecked by the providers' own rate limits.
    """

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name

    async def generate(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[Tool] | None = None,
    ) -> ProviderResponse:
        forced_failures = {
            name.strip() for name in settings.mock_provider_fail.split(",") if name.strip()
        }
        if self.provider_name in forced_failures:
            raise MockProviderError(
                f"Simulated failure for provider '{self.provider_name}' "
                "(MOCK_PROVIDER_FAIL)",
                503,
            )

        latency_s = random.uniform(
            settings.mock_provider_latency_ms_min / 1000,
            settings.mock_provider_latency_ms_max / 1000,
        )
        await asyncio.sleep(latency_s)

        user_text = " ".join(m.content or "" for m in messages)
        # Rough word-count proxy, only meant to give downstream cost-tracking
        # code non-zero, plausible-looking numbers to work with during a load
        # test - not a real tokenizer.
        input_tokens = max(1, len(user_text.split()))
        output_tokens = random.randint(20, 120)

        return ProviderResponse(
            content="[mock response]",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
