from dataclasses import dataclass

from app.config import settings
from app.providers.base import LLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.mock_provider import MockProvider
from app.providers.openai_provider import OpenAIProvider
from app.schemas.chat import ChatMessage

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


def select_provider(model: str, messages: list[ChatMessage]) -> RoutingDecision:
    if model == "auto":
        name = "openai" if _is_complex(messages) else "gemini"
        return _build_decision(name, PROVIDER_DEFAULT_MODELS[name])
    if model.startswith("gpt-"):
        return _build_decision("openai", model)
    if model.startswith("gemini-"):
        return _build_decision("gemini", model)
    raise RoutingError(f"Cannot map model '{model}' to a known provider")


def select_fallback(primary_provider_name: str) -> RoutingDecision:
    name = "openai" if primary_provider_name == "gemini" else "gemini"
    return _build_decision(name, PROVIDER_DEFAULT_MODELS[name])
