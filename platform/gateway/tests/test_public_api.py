from __future__ import annotations

import ai_platform_gateway as gw
import pytest
from ai_platform_gateway.errors import (
    AuthError,
    GatewayError,
    GatewayTimeout,
    InvalidRequest,
    ModelNotFound,
    ProviderUnavailable,
    RateLimited,
    error_from_status,
)
from ai_platform_gateway.gateway import Gateway
from ai_platform_gateway.types import ImagePart, Message, TextPart

from .fakes import FakeProvider, GatewayFactory


def test_module_level_functions_use_default_gateway(make_gateway: GatewayFactory) -> None:
    gateway = make_gateway([FakeProvider("a"), FakeProvider("b")])
    gw.set_gateway(gateway)
    try:
        assert gw.get_gateway() is gateway
        assert gw.complete("hi", "single").text == "a:ok"
        assert gw.embed(["x"], "vec").dimensions == 4
    finally:
        gw.set_gateway(None)


def test_default_gateway_lazily_created() -> None:
    gw.set_gateway(None)
    try:
        default = gw.get_gateway()
        assert isinstance(default, Gateway)
        assert "default" in default.config.aliases
        assert gw.get_gateway() is default
    finally:
        gw.set_gateway(None)


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (400, InvalidRequest),
        (422, InvalidRequest),
        (401, AuthError),
        (403, AuthError),
        (404, ModelNotFound),
        (408, GatewayTimeout),
        (409, ProviderUnavailable),
        (429, RateLimited),
        (500, ProviderUnavailable),
        (529, ProviderUnavailable),
        (302, GatewayError),
    ],
)
def test_error_from_status(status: int, cls: type[GatewayError]) -> None:
    err = error_from_status(status, "msg", "p")
    assert type(err) is cls
    assert err.status_code == status and err.provider == "p"


def test_message_helpers() -> None:
    img = ImagePart.from_bytes(b"abc", "image/png")
    assert img.raw_bytes() == b"abc"
    m = Message(role="user", content=[TextPart(text="a"), img, TextPart(text="b")])
    assert m.text() == "ab" and m.has_images()
    assert not Message(role="user", content="x").has_images()
    assert gw.GatewayError("x").describe() == "GatewayError"
