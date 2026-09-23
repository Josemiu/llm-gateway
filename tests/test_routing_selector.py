from datetime import datetime, timezone

import pytest

from app.config import settings
from app.providers.mock_provider import MockProvider
from app.routing.selector import (
    RoutingError,
    _apply_adaptive_policy,
    select_fallback,
    select_provider,
)
from app.schemas.chat import ChatMessage
from app.services.models import UsageRecord
from app.services.provider_stats_service import ProviderStats

NOW = datetime.now(timezone.utc)


def _user_message(content: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=content)]


def _stats(*, sample_count: int, error_rate: float, avg_latency_ms: float) -> ProviderStats:
    return ProviderStats(
        provider="irrelevant",
        sample_count=sample_count,
        error_rate=error_rate,
        avg_latency_ms=avg_latency_ms,
    )


async def _seed(session_factory, records: list[UsageRecord]) -> None:
    async with session_factory() as session:
        for record in records:
            session.add(record)
        await session.commit()


def _seed_record(*, provider: str, status: str, latency_ms: int = 100) -> UsageRecord:
    return UsageRecord(
        api_key="whoever",
        model_requested="auto",
        model_used="gemini-3.5-flash-lite" if provider == "gemini" else "gpt-4o-mini",
        provider=provider,
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.0,
        latency_ms=latency_ms,
        status=status,
        used_fallback=False,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_auto_short_plain_text_routes_to_gemini() -> None:
    decision = await select_provider("auto", _user_message("Hi there"))
    assert decision.provider_name == "gemini"
    assert decision.model == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_auto_code_keyword_routes_to_openai() -> None:
    decision = await select_provider("auto", _user_message("Write some code for me"))
    assert decision.provider_name == "openai"


@pytest.mark.asyncio
async def test_auto_analyze_keyword_routes_to_openai() -> None:
    decision = await select_provider("auto", _user_message("Please analyze this dataset"))
    assert decision.provider_name == "openai"


@pytest.mark.asyncio
async def test_auto_explain_in_detail_keyword_routes_to_openai() -> None:
    decision = await select_provider(
        "auto", _user_message("Can you explain in detail how TCP works")
    )
    assert decision.provider_name == "openai"


@pytest.mark.asyncio
async def test_auto_long_text_without_keywords_routes_to_openai() -> None:
    decision = await select_provider("auto", _user_message("a" * 250))
    assert decision.provider_name == "openai"


@pytest.mark.asyncio
async def test_explicit_openai_model_is_preserved() -> None:
    decision = await select_provider("gpt-4o", _user_message("hi"))
    assert decision.provider_name == "openai"
    assert decision.model == "gpt-4o"


@pytest.mark.asyncio
async def test_explicit_gemini_model_is_preserved() -> None:
    decision = await select_provider("gemini-2.0-flash", _user_message("hi"))
    assert decision.provider_name == "gemini"
    assert decision.model == "gemini-2.0-flash"


@pytest.mark.asyncio
async def test_unrecognized_model_raises_routing_error() -> None:
    with pytest.raises(RoutingError):
        await select_provider("claude-3-opus", _user_message("hi"))


def test_select_fallback_returns_the_other_provider() -> None:
    assert select_fallback("gemini").provider_name == "openai"
    assert select_fallback("openai").provider_name == "gemini"


@pytest.mark.asyncio
async def test_load_test_mode_uses_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "load_test_mode", True)

    decision = await select_provider("auto", _user_message("Hi there"))

    assert isinstance(decision.provider, MockProvider)
    assert decision.provider_name == "gemini"
    assert decision.model == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_load_test_mode_off_uses_real_provider_classes() -> None:
    decision = await select_provider("auto", _user_message("Hi there"))

    assert not isinstance(decision.provider, MockProvider)


# --- Adaptive routing policy (app/routing/selector.py::_apply_adaptive_policy) ---
# Unit tests against the pure function: precise control over each provider's
# stats without needing to seed exact row counts through the DB.


def test_adaptive_policy_keeps_candidate_when_both_healthy_similar_latency() -> None:
    stats = {
        "gemini": _stats(sample_count=30, error_rate=0.0, avg_latency_ms=500),
        "openai": _stats(sample_count=30, error_rate=0.0, avg_latency_ms=600),
    }
    assert _apply_adaptive_policy("gemini", stats) == "gemini"


def test_adaptive_policy_switches_away_from_unreliable_candidate() -> None:
    stats = {
        "gemini": _stats(sample_count=30, error_rate=0.8, avg_latency_ms=500),
        "openai": _stats(sample_count=30, error_rate=0.05, avg_latency_ms=600),
    }
    assert _apply_adaptive_policy("gemini", stats) == "openai"


def test_adaptive_policy_switches_to_lower_latency_provider_when_both_healthy() -> None:
    stats = {
        "gemini": _stats(sample_count=30, error_rate=0.0, avg_latency_ms=2000),
        "openai": _stats(sample_count=30, error_rate=0.0, avg_latency_ms=500),
    }
    # gemini is 4x slower than openai, well past the 2x default multiplier.
    assert _apply_adaptive_policy("gemini", stats) == "openai"


def test_adaptive_policy_ignores_providers_with_insufficient_samples() -> None:
    # gemini "looks" unreliable (1 error out of 2) but routing_min_samples
    # (20 by default) hasn't been met - too few requests to trust the rate.
    stats = {
        "gemini": _stats(sample_count=2, error_rate=0.5, avg_latency_ms=100),
        "openai": _stats(sample_count=30, error_rate=0.0, avg_latency_ms=100),
    }
    assert _apply_adaptive_policy("gemini", stats) == "gemini"


def test_adaptive_policy_keeps_candidate_when_no_healthy_alternative() -> None:
    stats = {
        "gemini": _stats(sample_count=30, error_rate=0.9, avg_latency_ms=500),
        "openai": _stats(sample_count=30, error_rate=0.9, avg_latency_ms=500),
    }
    assert _apply_adaptive_policy("gemini", stats) == "gemini"


def test_adaptive_policy_keeps_candidate_with_no_stats_at_all() -> None:
    assert _apply_adaptive_policy("gemini", {}) == "gemini"


# --- Integration: select_provider("auto", ...) wired to real DB stats ---


@pytest.mark.asyncio
async def test_select_provider_auto_overrides_heuristic_for_unreliable_provider(
    usage_db,
) -> None:
    # A short, simple prompt would normally route to gemini (see the
    # complexity heuristic tests above) - here gemini has been failing
    # consistently, openai has not.
    await _seed(
        usage_db,
        [_seed_record(provider="gemini", status="error") for _ in range(25)]
        + [_seed_record(provider="openai", status="success") for _ in range(25)],
    )

    decision = await select_provider("auto", _user_message("Hi there"))

    assert decision.provider_name == "openai"


@pytest.mark.asyncio
async def test_select_provider_auto_ignores_stats_below_min_samples(usage_db) -> None:
    await _seed(
        usage_db,
        [_seed_record(provider="gemini", status="error") for _ in range(3)],
    )

    decision = await select_provider("auto", _user_message("Hi there"))

    assert decision.provider_name == "gemini"


@pytest.mark.asyncio
async def test_select_provider_explicit_model_bypasses_adaptive_routing(usage_db) -> None:
    await _seed(
        usage_db,
        [_seed_record(provider="openai", status="error") for _ in range(30)],
    )

    decision = await select_provider("gpt-4o-mini", _user_message("hi"))

    assert decision.provider_name == "openai"
    assert decision.model == "gpt-4o-mini"
