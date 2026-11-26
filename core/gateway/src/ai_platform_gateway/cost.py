"""Cost calculation from token counts and ``models.yaml`` prices."""

from __future__ import annotations

from ai_platform_gateway.config import ModelEntry

_PER = 1_000_000


def compute_cost(
    entry: ModelEntry, input_tokens: int | None, output_tokens: int = 0
) -> float | None:
    """Return the USD cost of one call, or ``None`` when a needed price or count is unknown."""
    if input_tokens is None:
        return None
    price_in = entry.price_input_per_1m
    price_out = entry.price_output_per_1m
    threshold = entry.long_prompt_threshold_tokens
    if threshold is not None and input_tokens > threshold:
        price_in = entry.long_prompt_price_input_per_1m
        price_out = entry.long_prompt_price_output_per_1m
    if price_in is None:
        return None
    cost = input_tokens * price_in / _PER
    if output_tokens:
        if price_out is None:
            return None
        cost += output_tokens * price_out / _PER
    return round(cost, 10)
