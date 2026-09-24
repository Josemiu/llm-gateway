import base64
import json
import uuid

import httpx
from google import genai
from google.genai import errors, types

from app.config import settings
from app.providers.base import LLMProvider, ProviderError, ProviderResponse
from app.schemas.chat import ChatMessage, Tool, ToolCall, ToolCallFunction


class GeminiProviderError(ProviderError):
    pass


def _decode_thought_signature(provider_data: dict | None) -> bytes | None:
    encoded = (provider_data or {}).get("gemini_thought_signature")
    return base64.b64decode(encoded) if encoded else None


def _tool_call_id_to_name(messages: list[ChatMessage]) -> dict[str, str]:
    """Gemini's FunctionResponse is keyed by function *name*, not by the
    OpenAI-style call id our schema uses for tool_call_id - this rebuilds
    that mapping from the assistant messages earlier in the same
    conversation, so a "tool" role message can be translated correctly."""
    mapping: dict[str, str] = {}
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                mapping[tc.id] = tc.function.name
    return mapping


def _to_gemini_content(m: ChatMessage, id_to_name: dict[str, str]) -> types.Content:
    if m.role == "assistant" and m.tool_calls:
        parts = [
            types.Part(
                function_call=types.FunctionCall(
                    id=tc.id, name=tc.function.name, args=json.loads(tc.function.arguments)
                ),
                # Gemini 3.x ("thinking") models require this to be echoed
                # back on the same part in the next turn, or the API
                # rejects the request (verified live - see DECISIONS.md).
                thought_signature=_decode_thought_signature(tc.provider_data),
            )
            for tc in m.tool_calls
        ]
        if m.content:
            parts.insert(0, types.Part(text=m.content))
        return types.Content(role="model", parts=parts)
    if m.role == "tool":
        name = id_to_name.get(m.tool_call_id or "", "unknown_function")
        return types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name, response={"result": m.content}
                    )
                )
            ],
        )
    return types.Content(
        role="user" if m.role == "user" else "model",
        parts=[types.Part(text=m.content)],
    )


def _to_gemini_tools(tools: list[Tool]) -> list[types.Tool]:
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=t.function.name,
                    description=t.function.description,
                    parameters_json_schema=t.function.parameters,
                )
                for t in tools
            ]
        )
    ]


class GeminiProvider(LLMProvider):
    async def generate(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[Tool] | None = None,
    ) -> ProviderResponse:
        api_key = settings.gemini_api_key
        if not api_key:
            raise GeminiProviderError("GEMINI_API_KEY is not configured", 500)

        client = genai.Client(api_key=api_key)
        system_parts = [m.content for m in messages if m.role == "system"]
        id_to_name = _tool_call_id_to_name(messages)
        contents = [
            _to_gemini_content(m, id_to_name) for m in messages if m.role != "system"
        ]
        config = types.GenerateContentConfig(
            system_instruction="\n\n".join(p for p in system_parts if p) or None,
            tools=_to_gemini_tools(tools) if tools else None,
            # We decide whether/which tool to call ourselves (via the
            # returned tool_calls) - the SDK's automatic function calling
            # would try to invoke Python callables directly, which doesn't
            # apply here since we only ever pass declarations.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
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
        parts = response.candidates[0].content.parts if response.candidates else []
        text = "".join(p.text for p in parts if p.text) or None
        tool_calls = [
            ToolCall(
                id=p.function_call.id or f"call_{uuid.uuid4().hex}",
                function=ToolCallFunction(
                    name=p.function_call.name, arguments=json.dumps(p.function_call.args or {})
                ),
                provider_data=(
                    {
                        "gemini_thought_signature": base64.b64encode(
                            p.thought_signature
                        ).decode()
                    }
                    if p.thought_signature
                    else None
                ),
            )
            for p in parts
            if p.function_call
        ] or None

        return ProviderResponse(
            content=text,
            model=model,
            input_tokens=usage.prompt_token_count if usage else 0,
            output_tokens=usage.candidates_token_count if usage else 0,
            tool_calls=tool_calls,
        )
