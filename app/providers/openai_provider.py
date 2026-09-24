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
from app.schemas.chat import ChatMessage, Tool, ToolCall, ToolCallFunction


class OpenAIProviderError(ProviderError):
    pass


def _to_openai_message(m: ChatMessage) -> dict:
    msg: dict = {"role": m.role, "content": m.content}
    if m.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in m.tool_calls
        ]
    if m.tool_call_id:
        msg["tool_call_id"] = m.tool_call_id
    return msg


class OpenAIProvider(LLMProvider):
    async def generate(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[Tool] | None = None,
    ) -> ProviderResponse:
        api_key = settings.openai_api_key
        if not api_key:
            raise OpenAIProviderError("OPENAI_API_KEY is not configured", 500)

        client = AsyncOpenAI(api_key=api_key)
        payload = [_to_openai_message(m) for m in messages]
        kwargs = {}
        if tools:
            kwargs["tools"] = [t.model_dump() for t in tools]

        try:
            completion = await client.chat.completions.create(
                model=model, messages=payload, **kwargs
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
        message = completion.choices[0].message
        tool_calls = (
            [
                ToolCall(
                    id=tc.id,
                    function=ToolCallFunction(
                        name=tc.function.name, arguments=tc.function.arguments
                    ),
                )
                for tc in message.tool_calls
            ]
            if message.tool_calls
            else None
        )
        return ProviderResponse(
            content=message.content,
            model=completion.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            tool_calls=tool_calls,
        )
