import asyncio
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.middleware.rate_limit import enforce_rate_limit
from app.providers.base import ProviderError, ProviderResponse
from app.routing.selector import RoutingDecision, RoutingError, select_fallback, select_provider
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse, ChatMessage, Usage
from app.services.usage_service import record_usage

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
# (select_provider) -> Fallback (select_fallback) -> Provider (LLMProvider.generate)
# -> Usage tracking (record_usage, as a background task).
# Auth and rate limiting run before this function body, via the enforce_rate_limit
# dependency chain (it depends on verify_api_key, so FastAPI resolves auth first).
@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: ChatCompletionRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(enforce_rate_limit),
) -> ChatCompletionResponse:
    start = time.monotonic()

    try:
        primary = select_provider(request.model, request.messages)
    except RoutingError as exc:
        # Never reached a provider - nothing to track cost/latency-wise.
        raise HTTPException(status_code=400, detail=exc.message) from exc

    used_fallback = False
    attempt = primary
    try:
        result = await _generate_with_timeout(primary, request.messages)
    except ProviderError as primary_exc:
        used_fallback = True
        fallback = select_fallback(primary.provider_name)
        attempt = fallback
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
            latency_ms = int((time.monotonic() - start) * 1000)
            background_tasks.add_task(
                record_usage,
                api_key=api_key,
                model_requested=request.model,
                model_used=fallback.model,
                provider=fallback.provider_name,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                status="error",
                used_fallback=True,
            )
            detail = (
                f"Both providers failed. {primary.provider_name} ({primary.model}): "
                f"{primary_exc.message}; {fallback.provider_name} ({fallback.model}): "
                f"{fallback_exc.message}"
            )
            # A raised HTTPException would drop the background task attached
            # above (FastAPI/Starlette only runs tasks on the Response that
            # actually gets sent) - returning JSONResponse with `background=`
            # explicitly is what makes usage tracking work on the error path.
            return JSONResponse(
                status_code=fallback_exc.status_code,
                content={"detail": detail},
                background=background_tasks,
            )
        logger.info(
            "Fallback provider '%s' succeeded after '%s' failed",
            fallback.provider_name,
            primary.provider_name,
        )

    latency_ms = int((time.monotonic() - start) * 1000)
    background_tasks.add_task(
        record_usage,
        api_key=api_key,
        model_requested=request.model,
        model_used=result.model,
        provider=attempt.provider_name,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=latency_ms,
        status="success",
        used_fallback=used_fallback,
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
