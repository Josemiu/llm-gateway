import httpx
from google import genai
from google.genai import errors, types

from app.config import settings
from app.providers.base import LLMProvider, ProviderError, ProviderResponse
from app.schemas.chat import ChatMessage


class GeminiProviderError(ProviderError):
    pass


class GeminiProvider(LLMProvider):
    async def generate(
        self, model: str, messages: list[ChatMessage]
    ) -> ProviderResponse:
        api_key = settings.gemini_api_key
        if not api_key:
            raise GeminiProviderError("GEMINI_API_KEY is not configured", 500)

        client = genai.Client(api_key=api_key)
        system_parts = [m.content for m in messages if m.role == "system"]
        contents = [
            types.Content(
                role="user" if m.role == "user" else "model",
                parts=[types.Part(text=m.content)],
            )
            for m in messages
            if m.role != "system"
        ]
        config = types.GenerateContentConfig(
            system_instruction="\n\n".join(system_parts) or None,
        )

        try:
            response = await client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except errors.ClientError as exc:
            if exc.code in (401, 403):
                raise GeminiProviderError(
                    "Gemini rejected the configured API key", 500
                ) from exc
            if exc.code == 429:
                raise GeminiProviderError(
                    "Gemini rate limit exceeded, try again later", 429
                ) from exc
            raise GeminiProviderError(
                f"Gemini API error: {exc.message}", exc.code
            ) from exc
        except errors.ServerError as exc:
            raise GeminiProviderError(
                f"Gemini API error: {exc.message}", exc.code
            ) from exc
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise GeminiProviderError("Could not reach the Gemini API", 502) from exc
        except errors.APIError as exc:
            raise GeminiProviderError(
                f"Unexpected Gemini SDK error: {exc.message}", 500
            ) from exc

        usage = response.usage_metadata
        return ProviderResponse(
            content=response.text or "",
            model=model,
            input_tokens=usage.prompt_token_count if usage else 0,
            output_tokens=usage.candidates_token_count if usage else 0,
        )
