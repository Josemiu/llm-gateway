from typing import Literal

from pydantic import BaseModel, Field


class ToolFunction(BaseModel):
    name: str
    description: str
    parameters: dict = Field(default_factory=dict)


class Tool(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ToolCallFunction(BaseModel):
    name: str
    # JSON-encoded string (matches the OpenAI wire format) rather than a
    # parsed dict, so this schema stays provider-agnostic - GeminiProvider
    # gets args as a dict natively from the SDK and re-encodes it with
    # json.dumps to normalize to this same shape.
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction
    # Opaque, provider-specific data that must round-trip unmodified when a
    # caller sends this tool call back as part of the conversation history
    # (e.g. Gemini 3.x's thought_signature - see gemini_provider.py). A
    # caller never needs to understand this, just carry it forward as-is;
    # providers that don't need it (OpenAI) never set or read it.
    provider_data: dict | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    # None on an assistant message that only carries tool_calls (no text).
    content: str | None = None
    # Set on an assistant message that decided to call one or more tools.
    tool_calls: list[ToolCall] | None = None
    # Set on a "tool" role message - the result of executing tool_calls[i],
    # correlated back to it via this id.
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage] = Field(min_length=1)
    tools: list[Tool] | None = None


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    model: str
    usage: Usage
