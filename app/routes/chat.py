from fastapi import APIRouter, HTTPException

from app.providers.base import LLMProvider, ProviderError
from app.providers.gemini_provider import GeminiProvider
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse, Usage

router = APIRouter()

DEFAULT_MODEL = "gemini-3.5-flash-lite"


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: ChatCompletionRequest,
) -> ChatCompletionResponse:
    resolved_model = DEFAULT_MODEL if request.model == "auto" else request.model
    provider: LLMProvider = GeminiProvider()

    try:
        result = await provider.generate(model=resolved_model, messages=request.messages)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return ChatCompletionResponse(
        content=result.content,
        model=result.model,
        usage=Usage(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
        ),
    )
