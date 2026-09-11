import logging

logger = logging.getLogger(__name__)

# USD per 1,000,000 tokens. Reference prices from official sources (see
# DECISIONS.md for dates/links) - these drift over time and are not a
# real-time source of truth.
PRICING_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICING_PER_MILLION_TOKENS:
        logger.warning("No pricing data for model '%s', estimated cost = 0", model)
        return 0.0
    input_price, output_price = PRICING_PER_MILLION_TOKENS[model]
    return (input_tokens / 1_000_000) * input_price + (
        output_tokens / 1_000_000
    ) * output_price
