import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.rate_limit import enforce_rate_limit
from app.providers.base import ProviderError, ProviderResponse
from app.routing.selector import RoutingDecision, RoutingError, select_fallback, select_provider
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse, ChatMessage, Usage

router = APIRouter()
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30.0


async def _generate_with_timeout(
    decision: RoutingDecision, messages: list[ChatMessage]
) -> ProviderResponse:
    try:
        return await asyncio.wait_for(
            decision.provider.generate(model=decision.model, messages=messages),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise ProviderError(
            f"{decision.provider_name} did not respond within "
            f"{REQUEST_TIMEOUT_SECONDS:.0f}s",
            504,
        ) from exc


# Pipeline: Auth (verify_api_key) -> Rate Limit (enforce_rate_limit) -> Routing
# (select_provider) -> Fallback (select_fallback) -> Provider (LLMProvider.generate).
# Auth and rate limiting run before this function body, via the enforce_rate_limit
# dependency chain (it depends on verify_api_key, so FastAPI resolves auth first).
@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: ChatCompletionRequest,
    api_key: str = Depends(enforce_rate_limit),
) -> ChatCompletionResponse:
    try:
        primary = select_provider(request.model, request.messages)
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    try:
        result = await _generate_with_timeout(primary, request.messages)
    except ProviderError as primary_exc:
        fallback = select_fallback(primary.provider_name)
        logger.warning(
            "Primary provider '%s' failed (%s); retrying with '%s'",
            primary.provider_name,
            primary_exc.message,
            fallback.provider_name,
        )
        try:
            result = await _generate_with_timeout(fallback, request.messages)
        except ProviderError as fallback_exc:
            logger.error(
                "Fallback provider '%s' also failed (%s); no providers left",
                fallback.provider_name,
                fallback_exc.message,
            )
            detail = (
                f"Both providers failed. {primary.provider_name} ({primary.model}): "
                f"{primary_exc.message}; {fallback.provider_name} ({fallback.model}): "
                f"{fallback_exc.message}"
            )
            raise HTTPException(
                status_code=fallback_exc.status_code, detail=detail
            ) from fallback_exc
        logger.info(
            "Fallback provider '%s' succeeded after '%s' failed",
            fallback.provider_name,
            primary.provider_name,
        )

    return ChatCompletionResponse(
        content=result.content,
        model=result.model,
        usage=Usage(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
        ),
    )
