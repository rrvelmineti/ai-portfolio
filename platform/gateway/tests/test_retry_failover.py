from __future__ import annotations

import pytest
from ai_platform_gateway import (
    AllProvidersFailed,
    AuthError,
    GatewayTimeout,
    InvalidRequest,
    ModelNotFound,
    ProviderUnavailable,
    RateLimited,
)
from ai_platform_gateway.retry import RetryPolicy
from ai_platform_gateway.testing import FakeProvider, GatewayFactory


def rl() -> RateLimited:
    return RateLimited("slow down", status_code=429)


def s5xx() -> ProviderUnavailable:
    return ProviderUnavailable("oops", status_code=503)


def test_retries_on_429_then_succeeds(make_gateway: GatewayFactory, sleeps: list[float]) -> None:
    a = FakeProvider("a", [rl(), rl(), "ok"])
    res = make_gateway([a]).complete("hi", "single")
    assert res.text == "a:ok"
    assert res.attempts == 3
    assert len(a.requests) == 3
    assert len(sleeps) == 2
    assert all(0 < s <= 8.5 for s in sleeps)


def test_retries_on_5xx(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [s5xx(), "ok"])
    res = make_gateway([a]).complete("hi", "single")
    assert res.attempts == 2 and res.fallback_used is False


def test_retries_on_timeout(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [GatewayTimeout("timed out"), "ok"])
    gw = make_gateway([a])
    res = gw.complete("hi", "single", timeout_s=1.5)
    assert res.attempts == 2
    assert a.requests[0].timeout_s == 1.5


@pytest.mark.parametrize(
    "error",
    [
        InvalidRequest("bad", status_code=400),
        AuthError("no", status_code=401),
        AuthError("no", status_code=403),
    ],
)
def test_no_retry_on_400_401_403(make_gateway: GatewayFactory, error: Exception) -> None:
    a = FakeProvider("a", [error, "ok"])
    gw = make_gateway([a])
    with pytest.raises((InvalidRequest, AllProvidersFailed)):
        gw.complete("hi", "single")
    assert len(a.requests) == 1


def test_auth_error_skips_provider_immediately(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [AuthError("bad key", status_code=401)])
    b = FakeProvider("b")
    res = make_gateway([a, b, FakeProvider("c")]).complete("hi", "main")
    assert res.provider == "b"
    assert len(a.requests) == 1
    rec = res.attempt_records[0]
    assert (rec.outcome, rec.error_type, rec.attempts) == ("failed", "AuthError", 1)
    assert rec.reason == "AuthError (status=401)"


def test_model_not_found_fails_over(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [ModelNotFound("no model", status_code=404)])
    res = make_gateway([a, FakeProvider("b"), FakeProvider("c")]).complete("hi", "main")
    assert res.provider == "b" and len(a.requests) == 1


def test_failover_after_retries_exhausted(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [rl()])
    b = FakeProvider("b")
    res = make_gateway([a, b, FakeProvider("c")]).complete("hi", "main")
    assert len(a.requests) == 3
    assert res.provider == "b"
    assert res.fallback_used is True
    assert res.attempts == 4
    assert [r.outcome for r in res.attempt_records] == ["failed", "ok"]
    assert res.attempt_records[0].attempts == 3


def test_fallback_order_is_respected(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [s5xx()])
    b = FakeProvider("b", [AuthError("x", status_code=403)])
    c = FakeProvider("c")
    res = make_gateway([a, b, c]).complete("hi", "main")
    assert res.provider == "c"
    assert [r.provider for r in res.attempt_records] == ["a", "b", "c"]


def test_all_providers_failed_contents(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [rl()])
    b = FakeProvider("b", [AuthError("bad key sk-secret", status_code=401)])
    c = FakeProvider("c", [GatewayTimeout("timed out")], required_env=("C_KEY",))
    gw = make_gateway([a, b, c], environ={"C_KEY": "x"})
    with pytest.raises(AllProvidersFailed) as info:
        gw.complete("hi", "main")
    attempts = info.value.attempts
    assert [(r.provider, r.model, r.outcome, r.error_type, r.attempts) for r in attempts] == [
        ("a", "a-chat", "failed", "RateLimited", 3),
        ("b", "b-chat", "failed", "AuthError", 1),
        ("c", "c-chat", "failed", "GatewayTimeout", 3),
    ]
    assert attempts[0].reason == "RateLimited (status=429)"
    assert info.value.alias == "main"
    assert "sk-secret" not in str(info.value)


def test_all_failed_includes_unconfigured_skips(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", required_env=("A_KEY",))
    b = FakeProvider("b", [s5xx()])
    c = FakeProvider("c", [s5xx()])
    with pytest.raises(AllProvidersFailed) as info:
        make_gateway([a, b, c]).complete("hi", "main")
    assert [r.outcome for r in info.value.attempts] == ["skipped", "failed", "failed"]


def test_retry_policy_attempt_count_on_failure() -> None:
    policy = RetryPolicy(max_attempts=2, sleep=lambda _s: None)
    calls = 0

    def boom() -> None:
        nonlocal calls
        calls += 1
        raise RateLimited("x")

    with pytest.raises(RateLimited) as info:
        policy.call(boom)
    assert calls == 2
    assert info.value.gateway_attempts == 2  # type: ignore[attr-defined]
