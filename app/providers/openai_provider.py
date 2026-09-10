from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)

from app.config import settings
from app.providers.base import LLMProvider, ProviderError, ProviderResponse
from app.schemas.chat import ChatMessage


class OpenAIProviderError(ProviderError):
    pass


class OpenAIProvider(LLMProvider):
    async def generate(
        self, model: str, messages: list[ChatMessage]
    ) -> ProviderResponse:
        api_key = settings.openai_api_key
        if not api_key:
            raise OpenAIProviderError("OPENAI_API_KEY is not configured", 500)

        client = AsyncOpenAI(api_key=api_key)
        payload = [{"role": m.role, "content": m.content} for m in messages]

        try:
            completion = await client.chat.completions.create(
                model=model, messages=payload
            )
        except AuthenticationError as exc:
            raise OpenAIProviderError(
                "OpenAI rejected the configured API key", 500
            ) from exc
        except RateLimitError as exc:
            raise OpenAIProviderError(
                "OpenAI rate limit exceeded, try again later", 429
            ) from exc
        except APIConnectionError as exc:
            raise OpenAIProviderError("Could not reach the OpenAI API", 502) from exc
        except APIStatusError as exc:
            raise OpenAIProviderError(
                f"OpenAI API error: {exc.message}", exc.status_code
            ) from exc
        except OpenAIError as exc:
            raise OpenAIProviderError(
                f"Unexpected OpenAI SDK error: {exc}", 500
            ) from exc

        usage = completion.usage
        return ProviderResponse(
            content=completion.choices[0].message.content or "",
            model=completion.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
