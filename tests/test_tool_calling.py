import json
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from google.genai import types as gtypes
from httpx import ASGITransport, AsyncClient
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from app.main import app
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider
from app.schemas.chat import ChatMessage, Tool, ToolCall, ToolCallFunction, ToolFunction

WEATHER_TOOL = Tool(
    function=ToolFunction(
        name="get_weather",
        description="Get the current weather for a city",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
)


# --- OpenAIProvider ---------------------------------------------------


def _openai_tool_call_response() -> ChatCompletion:
    return ChatCompletion(
        id="chatcmpl-1",
        object="chat.completion",
        created=0,
        model="gpt-4o-mini",
        choices=[
            Choice(
                index=0,
                finish_reason="tool_calls",
                message=ChatCompletionMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call_abc",
                            type="function",
                            function=Function(
                                name="get_weather", arguments='{"city": "Tokyo"}'
                            ),
                        )
                    ],
                ),
            )
        ],
        usage=CompletionUsage(
            prompt_tokens=20, completion_tokens=8, total_tokens=28
        ),
    )


@pytest.mark.asyncio
async def test_openai_provider_parses_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.providers.openai_provider.settings.openai_api_key", "k")

    with patch("app.providers.openai_provider.AsyncOpenAI") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_openai_tool_call_response()
        )

        result = await OpenAIProvider().generate(
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Weather in Tokyo?")],
            tools=[WEATHER_TOOL],
        )

    assert result.content is None
    assert result.tool_calls == [
        ToolCall(
            id="call_abc",
            function=ToolCallFunction(name="get_weather", arguments='{"city": "Tokyo"}'),
        )
    ]

    # The tool declaration reached the SDK call.
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["tools"][0]["function"]["name"] == "get_weather"


@pytest.mark.asyncio
async def test_openai_provider_sends_tool_result_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.providers.openai_provider.settings.openai_api_key", "k")

    with patch("app.providers.openai_provider.AsyncOpenAI") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat.completions.create = AsyncMock(
            return_value=_openai_tool_call_response()
        )

        messages = [
            ChatMessage(role="user", content="Weather in Tokyo?"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_abc",
                        function=ToolCallFunction(
                            name="get_weather", arguments='{"city": "Tokyo"}'
                        ),
                    )
                ],
            ),
            ChatMessage(role="tool", tool_call_id="call_abc", content="21C, sunny"),
        ]
        await OpenAIProvider().generate(model="gpt-4o-mini", messages=messages)

    payload = mock_client.chat.completions.create.call_args.kwargs["messages"]
    assert payload[1]["tool_calls"][0]["id"] == "call_abc"
    assert payload[2] == {
        "role": "tool",
        "content": "21C, sunny",
        "tool_call_id": "call_abc",
    }


# --- GeminiProvider -----------------------------------------------------


def _gemini_tool_call_response() -> gtypes.GenerateContentResponse:
    return gtypes.GenerateContentResponse(
        candidates=[
            gtypes.Candidate(
                content=gtypes.Content(
                    role="model",
                    parts=[
                        gtypes.Part(
                            function_call=gtypes.FunctionCall(
                                id="call_xyz", name="get_weather", args={"city": "Tokyo"}
                            ),
                            thought_signature=b"opaque-signature-bytes",
                        )
                    ],
                ),
                finish_reason="STOP",
                index=0,
            )
        ],
        usage_metadata=gtypes.GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=6, total_token_count=18
        ),
    )


@pytest.mark.asyncio
async def test_gemini_provider_parses_tool_calls_and_thought_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.providers.gemini_provider.settings.gemini_api_key", "k")

    with patch("app.providers.gemini_provider.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=_gemini_tool_call_response()
        )

        result = await GeminiProvider().generate(
            model="gemini-3.5-flash-lite",
            messages=[ChatMessage(role="user", content="Weather in Tokyo?")],
            tools=[WEATHER_TOOL],
        )

    assert result.content is None
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.id == "call_xyz"
    assert json.loads(tc.function.arguments) == {"city": "Tokyo"}
    assert tc.provider_data["gemini_thought_signature"]


@pytest.mark.asyncio
async def test_gemini_provider_echoes_thought_signature_on_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.providers.gemini_provider.settings.gemini_api_key", "k")

    with patch("app.providers.gemini_provider.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=_gemini_tool_call_response()
        )

        messages = [
            ChatMessage(role="user", content="Weather in Tokyo?"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_xyz",
                        function=ToolCallFunction(
                            name="get_weather", arguments='{"city": "Tokyo"}'
                        ),
                        provider_data={
                            "gemini_thought_signature": "b3BhcXVlLXNpZ25hdHVyZS1ieXRlcw=="
                        },
                    )
                ],
            ),
            ChatMessage(role="tool", tool_call_id="call_xyz", content="21C, sunny"),
        ]
        await GeminiProvider().generate(model="gemini-3.5-flash-lite", messages=messages)

    call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
    contents = call_kwargs["contents"]
    assistant_part = contents[1].parts[0]
    assert assistant_part.thought_signature == b"opaque-signature-bytes"
    tool_result_part = contents[2].parts[0]
    assert tool_result_part.function_response.name == "get_weather"
    assert tool_result_part.function_response.response == {"result": "21C, sunny"}


# --- End-to-end through /v1/chat/completions -----------------------------

TEST_API_KEY = "tool-calling-test-key"


@pytest.mark.asyncio
async def test_chat_endpoint_returns_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.providers.gemini_provider.settings.gemini_api_key", "k")
    monkeypatch.setattr("app.middleware.auth.settings.valid_api_keys", TEST_API_KEY)
    monkeypatch.setattr(
        "app.middleware.rate_limit.redis_client", fakeredis.FakeAsyncRedis()
    )

    with patch("app.providers.gemini_provider.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=_gemini_tool_call_response()
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3.5-flash-lite",
                    "messages": [{"role": "user", "content": "Weather in Tokyo?"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "description": "Get current weather",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"city": {"type": "string"}},
                                },
                            },
                        }
                    ],
                },
                headers={"X-API-Key": TEST_API_KEY},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] is None
    assert body["tool_calls"][0]["function"]["name"] == "get_weather"
