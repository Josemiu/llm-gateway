import pytest

from app.routing.selector import RoutingError, select_fallback, select_provider
from app.schemas.chat import ChatMessage


def _user_message(content: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=content)]


def test_auto_short_plain_text_routes_to_gemini() -> None:
    decision = select_provider("auto", _user_message("Hi there"))
    assert decision.provider_name == "gemini"
    assert decision.model == "gemini-3.5-flash-lite"


def test_auto_code_keyword_routes_to_openai() -> None:
    decision = select_provider("auto", _user_message("Write some code for me"))
    assert decision.provider_name == "openai"


def test_auto_analyze_keyword_routes_to_openai() -> None:
    decision = select_provider("auto", _user_message("Please analyze this dataset"))
    assert decision.provider_name == "openai"


def test_auto_explain_in_detail_keyword_routes_to_openai() -> None:
    decision = select_provider(
        "auto", _user_message("Can you explain in detail how TCP works")
    )
    assert decision.provider_name == "openai"


def test_auto_long_text_without_keywords_routes_to_openai() -> None:
    decision = select_provider("auto", _user_message("a" * 250))
    assert decision.provider_name == "openai"


def test_explicit_openai_model_is_preserved() -> None:
    decision = select_provider("gpt-4o", _user_message("hi"))
    assert decision.provider_name == "openai"
    assert decision.model == "gpt-4o"


def test_explicit_gemini_model_is_preserved() -> None:
    decision = select_provider("gemini-2.0-flash", _user_message("hi"))
    assert decision.provider_name == "gemini"
    assert decision.model == "gemini-2.0-flash"


def test_unrecognized_model_raises_routing_error() -> None:
    with pytest.raises(RoutingError):
        select_provider("claude-3-opus", _user_message("hi"))


def test_select_fallback_returns_the_other_provider() -> None:
    assert select_fallback("gemini").provider_name == "openai"
    assert select_fallback("openai").provider_name == "gemini"
