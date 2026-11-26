from __future__ import annotations

import pytest
from ai_platform_gateway.config import ModelEntry
from ai_platform_gateway.cost import compute_cost

from .fakes import FakeProvider, GatewayFactory, completion


def entry(**kw: object) -> ModelEntry:
    return ModelEntry.model_validate({"provider": "p", "model": "m", **kw})


def test_cost_from_prices() -> None:
    e = entry(price_input_per_1m=2.0, price_output_per_1m=10.0)
    assert compute_cost(e, 1_000_000, 0) == 2.0
    assert compute_cost(e, 1000, 500) == pytest.approx(0.002 + 0.005)


def test_cost_unknown_when_price_null() -> None:
    assert compute_cost(entry(), 100, 10) is None
    assert compute_cost(entry(price_input_per_1m=1.0), 100, 10) is None
    assert compute_cost(entry(price_input_per_1m=1.0), None) is None


def test_embedding_cost_needs_input_price_only() -> None:
    e = entry(kind="embed", price_input_per_1m=0.02)
    assert compute_cost(e, 500_000) == pytest.approx(0.01)


def test_long_prompt_tier() -> None:
    e = entry(
        price_input_per_1m=0.10,
        price_output_per_1m=0.50,
        long_prompt_threshold_tokens=100_000,
        long_prompt_price_input_per_1m=0.50,
        long_prompt_price_output_per_1m=2.50,
    )
    assert compute_cost(e, 100_000, 1000) == pytest.approx(0.01 + 0.0005)
    assert compute_cost(e, 200_000, 1000) == pytest.approx(0.1 + 0.0025)


def test_result_cost_and_tokens(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [completion("x", inp=1200, out=300)])
    res = make_gateway([a]).complete("hi", "single")
    assert (res.input_tokens, res.output_tokens) == (1200, 300)
    assert res.cost_usd == pytest.approx(1200 * 1.0 / 1e6 + 300 * 2.0 / 1e6)


def test_result_cost_null_for_unverified_price(make_gateway: GatewayFactory) -> None:
    res = make_gateway([FakeProvider("c")]).complete("hi", "nocost")
    assert res.cost_usd is None
