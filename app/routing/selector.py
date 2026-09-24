import logging
from dataclasses import dataclass
from enum import Enum, auto

from app.config import settings
from app.providers.base import LLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.mock_provider import MockProvider
from app.providers.openai_provider import OpenAIProvider
from app.schemas.chat import ChatMessage
from app.services.provider_stats_service import ProviderStats, get_provider_stats
from app.telemetry import tracer

logger = logging.getLogger(__name__)

# Umbral y keywords elegidos a mano, sin tuning con datos reales todavía
# (ver DECISIONS.md: "Heurística de routing 'auto': simple y explicable, no ML").
COMPLEXITY_LENGTH_THRESHOLD = 200
COMPLEXITY_KEYWORDS = ("code", "analyze", "explain in detail")

PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "gemini": "gemini-3.5-flash-lite",
    "openai": "gpt-4o-mini",
}
_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


class RoutingError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class RoutingDecision:
    provider_name: str
    provider: LLMProvider
    model: str


def _is_complex(messages: list[ChatMessage]) -> bool:
    # Solo se mira lo que pidió el usuario: el system prompt no debería
    # influir en qué provider responde, y el historial del asistente
    # tampoco aporta a "qué tan elaborado es el pedido actual".
    user_text = " ".join(m.content for m in messages if m.role == "user").lower()
    if len(user_text) >= COMPLEXITY_LENGTH_THRESHOLD:
        return True
    return any(keyword in user_text for keyword in COMPLEXITY_KEYWORDS)


def _build_decision(provider_name: str, model: str) -> RoutingDecision:
    # settings.load_test_mode is load-testing-only (see DECISIONS.md and
    # app/providers/mock_provider.py); false by default so normal dev/prod
    # routing is unaffected.
    provider: LLMProvider = (
        MockProvider(provider_name)
        if settings.load_test_mode
        else _PROVIDER_CLASSES[provider_name]()
    )
    return RoutingDecision(
        provider_name=provider_name,
        provider=provider,
        model=model,
    )


def _other_provider(provider_name: str) -> str:
    return "openai" if provider_name == "gemini" else "gemini"


class _Health(Enum):
    UNKNOWN = auto()  # fewer than routing_min_samples in the window - no real evidence either way
    HEALTHY = auto()
    UNRELIABLE = auto()


def _health(stats: ProviderStats | None) -> _Health:
    if stats is None or stats.sample_count < settings.routing_min_samples:
        return _Health.UNKNOWN
    if stats.error_rate > settings.routing_error_rate_threshold:
        return _Health.UNRELIABLE
    return _Health.HEALTHY


def _apply_adaptive_policy(
    candidate: str, stats_by_provider: dict[str, ProviderStats]
) -> str:
    """Adjusts the complexity heuristic's provider pick using real recent
    stats from provider_stats_service - reliability first, then latency
    (see DECISIONS.md - "Routing adaptativo"). Only ever called for
    model="auto"; explicit model requests never reach this. With fewer than
    `routing_min_samples` samples a provider's stats are UNKNOWN (not
    HEALTHY or UNRELIABLE) and never drive a decision - too few requests
    make error_rate/avg_latency noisy (e.g. 1 failure out of 2 requests
    looks like a 50% error rate).
    """
    other = _other_provider(candidate)
    candidate_health = _health(stats_by_provider.get(candidate))
    other_health = _health(stats_by_provider.get(other))

    if candidate_health is _Health.UNRELIABLE and other_health is not _Health.UNRELIABLE:
        logger.info(
            "Adaptive routing: '%s' error rate too high recently, preferring '%s'",
            candidate,
            other,
        )
        return other

    if candidate_health is _Health.HEALTHY and other_health is _Health.HEALTHY:
        candidate_latency = stats_by_provider[candidate].avg_latency_ms
        other_latency = stats_by_provider[other].avg_latency_ms
        if (
            other_latency > 0
            and candidate_latency
            > other_latency * settings.routing_latency_degradation_multiplier
        ):
            logger.info(
                "Adaptive routing: '%s' avg latency (%.0fms) is over %.1fx '%s' "
                "(%.0fms), preferring '%s'",
                candidate,
                candidate_latency,
                settings.routing_latency_degradation_multiplier,
                other,
                other_latency,
                other,
            )
            return other

    return candidate


async def select_provider(model: str, messages: list[ChatMessage]) -> RoutingDecision:
    with tracer.start_as_current_span("routing.select_provider") as span:
        span.set_attribute("model.requested", model)
        if model == "auto":
            name = "openai" if _is_complex(messages) else "gemini"
            try:
                stats_by_provider = await get_provider_stats(
                    settings.routing_stats_window_minutes
                )
            except Exception:
                # Adaptive routing is a nice-to-have on top of the base
                # heuristic, not a dependency the whole request should die
                # on - same fail-open philosophy as Redis for rate limiting
                # (see DECISIONS.md). An unreachable Postgres must not turn
                # every "auto" request into a 500.
                logger.warning(
                    "Could not fetch provider stats for adaptive routing; "
                    "falling back to the base heuristic",
                    exc_info=True,
                )
                stats_by_provider = {}
            name = _apply_adaptive_policy(name, stats_by_provider)
            span.set_attribute("provider.selected", name)
            return _build_decision(name, PROVIDER_DEFAULT_MODELS[name])
        if model.startswith("gpt-"):
            span.set_attribute("provider.selected", "openai")
            return _build_decision("openai", model)
        if model.startswith("gemini-"):
            span.set_attribute("provider.selected", "gemini")
            return _build_decision("gemini", model)
        raise RoutingError(f"Cannot map model '{model}' to a known provider")


def select_fallback(primary_provider_name: str) -> RoutingDecision:
    name = "openai" if primary_provider_name == "gemini" else "gemini"
    return _build_decision(name, PROVIDER_DEFAULT_MODELS[name])
