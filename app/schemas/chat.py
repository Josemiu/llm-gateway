from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage] = Field(min_length=1)


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    content: str
    model: str
    usage: Usage
