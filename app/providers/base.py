from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.schemas.chat import ChatMessage


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self, model: str, messages: list[ChatMessage]
    ) -> ProviderResponse: ...
