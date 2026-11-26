from __future__ import annotations

import pytest
from ai_platform_gateway import SchemaValidationError
from ai_platform_gateway.structured import merge_system, strip_fences
from ai_platform_gateway.testing import FakeProvider, GatewayFactory, completion
from pydantic import BaseModel


class Claim(BaseModel):
    claim_id: str
    amount: float


def test_schema_success_first_try(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [completion('{"claim_id": "C-1", "amount": 12.5}')])
    res = make_gateway([a]).complete("extract", "single", response_schema=Claim)
    assert res.parsed == Claim(claim_id="C-1", amount=12.5)
    req = a.requests[0]
    assert req.json_mode is True
    assert req.system is not None and '"claim_id"' in req.system


def test_schema_accepts_code_fences(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [completion('```json\n{"claim_id": "C-2", "amount": 1}\n```')])
    res = make_gateway([a]).complete("extract", "single", response_schema=Claim)
    assert isinstance(res.parsed, Claim) and res.parsed.claim_id == "C-2"


def test_schema_retry_once_with_error_appended(make_gateway: GatewayFactory) -> None:
    a = FakeProvider(
        "a",
        [
            completion('{"claim_id": "C-3"}', inp=10, out=5),
            completion('{"claim_id": "C-3", "amount": 9}', inp=20, out=6),
        ],
    )
    res = make_gateway([a]).complete(
        "extract", "single", system="You extract claims.", response_schema=Claim
    )
    assert res.parsed == Claim(claim_id="C-3", amount=9)
    assert len(a.requests) == 2
    retry_msgs = a.requests[1].messages
    assert retry_msgs[1].role == "assistant" and retry_msgs[1].text() == '{"claim_id": "C-3"}'
    assert "amount" in retry_msgs[2].text() and "did not validate" in retry_msgs[2].text()
    assert a.requests[1].system is not None
    assert a.requests[1].system.startswith("You extract claims.")
    assert (res.input_tokens, res.output_tokens) == (30, 11)
    assert res.cost_usd == pytest.approx(30 * 1.0 / 1e6 + 11 * 2.0 / 1e6)
    assert res.attempts == 2


def test_schema_failure_after_retry(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [completion("not json"), completion('{"claim_id": 1}')])
    with pytest.raises(SchemaValidationError) as info:
        make_gateway([a]).complete("extract", "single", response_schema=Claim)
    assert len(a.requests) == 2
    assert info.value.raw_text == '{"claim_id": 1}'
    assert "amount" in info.value.errors


def test_schema_cost_unknown_propagates(make_gateway: GatewayFactory) -> None:
    c = FakeProvider("c", [completion("{}"), completion('{"claim_id": "x", "amount": 1}')])
    res = make_gateway([c]).complete("extract", "nocost", response_schema=Claim)
    assert res.cost_usd is None


def test_helpers() -> None:
    assert merge_system("sys", None) == "sys"
    assert merge_system(None, Claim) is not None
    assert strip_fences("  {}  ") == "{}"
