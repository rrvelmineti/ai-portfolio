from __future__ import annotations

import pytest
from ai_platform_gateway import (
    AllProvidersFailed,
    ImagePart,
    InvalidRequest,
    Message,
    NoProvidersConfigured,
    NotSupported,
    TextPart,
)
from ai_platform_gateway.testing import FakeProvider, GatewayFactory, NoEmbedProvider


def test_routes_alias_to_first_provider(make_gateway: GatewayFactory) -> None:
    a, b = FakeProvider("a"), FakeProvider("b")
    gw = make_gateway([a, b, FakeProvider("c")])
    res = gw.complete("hi", "main")
    assert res.text == "a:ok"
    assert (res.provider, res.model, res.alias) == ("a", "a-chat", "main")
    assert res.fallback_used is False
    assert res.attempts == 1
    assert len(a.requests) == 1 and not b.requests
    assert a.requests[0].messages == [Message(role="user", content="hi")]


def test_unknown_alias_is_invalid(make_gateway: GatewayFactory) -> None:
    gw = make_gateway([FakeProvider("a")])
    with pytest.raises(InvalidRequest, match="unknown model alias"):
        gw.complete("hi", "nope")


def test_alias_kind_mismatch(make_gateway: GatewayFactory) -> None:
    gw = make_gateway([FakeProvider("a"), FakeProvider("b")])
    with pytest.raises(InvalidRequest, match="embed"):
        gw.complete("hi", "vec")
    with pytest.raises(InvalidRequest, match="complete"):
        gw.embed("hi", "main")


def test_empty_inputs_rejected(make_gateway: GatewayFactory) -> None:
    gw = make_gateway([FakeProvider("a"), FakeProvider("b")])
    with pytest.raises(InvalidRequest):
        gw.complete([], "main")
    with pytest.raises(InvalidRequest):
        gw.embed([], "vec")


def test_skips_unconfigured_providers_with_warning(
    make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture
) -> None:
    a = FakeProvider("a", required_env=("A_KEY",))
    b = FakeProvider("b", required_env=("B_KEY",))
    gw = make_gateway([a, b, FakeProvider("c")], environ={"B_KEY": "x"})
    with caplog.at_level("WARNING"):
        res = gw.complete("hi", "main")
    assert res.provider == "b"
    assert res.fallback_used is True
    assert not a.requests
    assert res.attempt_records[0].outcome == "skipped"
    assert res.attempt_records[0].reason == "missing env: A_KEY"
    skipped = [r for r in caplog.records if r.getMessage() == "gateway.provider_skipped"]
    assert skipped and skipped[0].fields["missing_env"] == ["A_KEY"]  # type: ignore[attr-defined]


def test_no_providers_configured(make_gateway: GatewayFactory) -> None:
    gw = make_gateway(
        [
            FakeProvider("a", required_env=("A_KEY",)),
            FakeProvider("b", required_env=("B_KEY", "B_REGION")),
            FakeProvider("c", required_env=("C_KEY",)),
        ]
    )
    with pytest.raises(NoProvidersConfigured) as info:
        gw.complete("hi", "main")
    err = info.value
    assert not isinstance(err, AllProvidersFailed)
    assert err.missing_env == {
        "a/chat": ["A_KEY"],
        "b/chat": ["B_KEY", "B_REGION"],
        "c/chat": ["C_KEY"],
    }
    assert "A_KEY" in str(err) and "B_REGION" in str(err)


def test_model_id_from_env_var(make_gateway: GatewayFactory) -> None:
    d = FakeProvider("d")
    gw = make_gateway([d], environ={"D_DEPLOYMENT": "my-deploy"})
    res = gw.complete("hi", "envmod")
    assert res.model == "my-deploy"
    assert d.requests[0].model == "my-deploy"
    with pytest.raises(NoProvidersConfigured, match="D_DEPLOYMENT"):
        make_gateway([FakeProvider("d")]).complete("hi", "envmod")


def test_temperature_dropped_when_model_does_not_support_it(
    make_gateway: GatewayFactory, caplog: pytest.LogCaptureFixture
) -> None:
    a = FakeProvider("a", [NotSupported("no")])
    b = FakeProvider("b")
    gw = make_gateway([a, b, FakeProvider("c")])
    with caplog.at_level("DEBUG"):
        gw.complete("hi", "main", temperature=0.7)
    assert a.requests[0].temperature == 0.7
    assert b.requests[0].temperature is None
    assert any(r.getMessage() == "gateway.temperature_dropped" for r in caplog.records)


def test_images_skip_entries_without_image_support(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [NotSupported("down")])
    b, c = FakeProvider("b"), FakeProvider("c")
    gw = make_gateway([a, b, c])
    msg = Message(
        role="user",
        content=[TextPart(text="what is this?"), ImagePart.from_bytes(b"\x89PNG", "image/png")],
    )
    res = gw.complete([msg], "main")
    assert res.provider == "c"
    assert not b.requests  # b/chat has supports_images: false
    b_rec = res.attempt_records[1]
    assert (b_rec.provider, b_rec.outcome, b_rec.error_type) == ("b", "skipped", "NotSupported")


def test_adapter_not_supported_skips_to_next(make_gateway: GatewayFactory) -> None:
    """A capability mismatch raised by the adapter itself must not stop fallback."""
    a = FakeProvider("a", [NotSupported("a adapter does not accept images", provider="a")])
    gw = make_gateway([a, FakeProvider("b"), FakeProvider("c")])
    res = gw.complete("hi", "main")
    assert res.provider == "b"
    first = res.attempt_records[0]
    assert (first.outcome, first.error_type) == ("skipped", "NotSupported")


def test_embed_not_supported_skips_to_next(make_gateway: GatewayFactory) -> None:
    a = NoEmbedProvider("a")
    b = FakeProvider("b")
    gw = make_gateway([a, b])
    res = gw.embed(["x", "y"], "vec")
    assert res.provider == "b"
    assert res.dimensions == 4 and len(res.vectors) == 2
    assert res.fallback_used is True
    assert res.attempt_records[0].outcome == "skipped"
    assert res.attempt_records[0].error_type == "NotSupported"
    assert b.embed_requests[0].dimensions == 4


def test_embed_dimension_capability(make_gateway: GatewayFactory) -> None:
    c, b = FakeProvider("c"), FakeProvider("b")
    gw = make_gateway([c, b])
    res = gw.embed("x", "vec256")
    assert res.provider == "b"
    assert not c.embed_requests  # c only supports 256-dim, alias needs 4
    assert res.attempt_records[0].reason == "NotSupported"


def test_embed_wrong_dimensions_from_provider_fails_over(make_gateway: GatewayFactory) -> None:
    from ai_platform_gateway.providers.base import ProviderEmbedding

    a = FakeProvider("a", embed_steps=[ProviderEmbedding(vectors=[[1.0, 2.0]], tokens=1)])
    gw = make_gateway([a, FakeProvider("b")])
    assert gw.embed("x", "vec").provider == "b"
    a2 = FakeProvider("a", embed_steps=[ProviderEmbedding(vectors=[], tokens=1)])
    assert make_gateway([a2, FakeProvider("b")]).embed("x", "vec").provider == "b"


def test_invalid_request_raises_immediately(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [InvalidRequest("bad params", provider="a", status_code=400)])
    b = FakeProvider("b")
    gw = make_gateway([a, b, FakeProvider("c")])
    with pytest.raises(InvalidRequest):
        gw.complete("hi", "main")
    assert len(a.requests) == 1
    assert not b.requests


def test_unexpected_adapter_exception_fails_over(make_gateway: GatewayFactory) -> None:
    a = FakeProvider("a", [RuntimeError("bug")])
    gw = make_gateway([a, FakeProvider("b"), FakeProvider("c")])
    res = gw.complete("hi", "main")
    assert res.provider == "b"
    assert res.attempt_records[0].reason == "GatewayError"


def test_embed_single_string(make_gateway: GatewayFactory) -> None:
    gw = make_gateway([FakeProvider("a"), FakeProvider("b")])
    res = gw.embed("hello", "vec", request_id="rid-1")
    assert res.request_id == "rid-1"
    assert res.tokens == 10
    assert res.cost_usd == pytest.approx(10 * 0.5 / 1_000_000)
