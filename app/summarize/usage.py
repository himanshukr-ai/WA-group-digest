from __future__ import annotations

import logging

from app.config import Settings

logger = logging.getLogger(__name__)


def estimate_cost(input_tokens: int, output_tokens: int, settings: Settings) -> float:
    return (
        input_tokens / 1_000_000 * settings.anthropic_price_input_per_mtok
        + output_tokens / 1_000_000 * settings.anthropic_price_output_per_mtok
    )


def log_usage(label: str, input_tokens: int, output_tokens: int, settings: Settings) -> float:
    cost = estimate_cost(input_tokens, output_tokens, settings)
    logger.info(
        "%s: %d input tokens, %d output tokens, ~$%.4f (model=%s)",
        label, input_tokens, output_tokens, cost, settings.anthropic_model,
    )
    return cost
