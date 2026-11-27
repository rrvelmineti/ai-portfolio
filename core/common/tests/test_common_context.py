from __future__ import annotations

import uuid
from typing import Any

import pytest
from ai_platform_common import context
from ai_platform_common.context import (
    RequestContextMiddleware,
    clean_request_id,
    current,
    current_request_id,
    ensure_request_context,
    request_context,
    set_user,
)


def test_clean_request_id() -> None:
    assert clean_request_id("abc-123") == "abc-123"
    assert len(clean_request_id("bad id\n")) == 36
    assert len(clean_request_id(None)) == 36
    assert len(clean_request_id("x" * 101)) == 36


def test_request_context_scoping() -> None:
    assert current() is None and current_request_id() is None
    set_user(uuid.uuid4(), uuid.uuid4())  # no-op outside a context
    with request_context("job-1", product="claims") as ctx:
        assert current() is ctx and current_request_id() == "job-1"
        tid, uid = uuid.uuid4(), uuid.uuid4()
        set_user(tid, uid)
        assert ctx.log_fields() == {"request_id": "job-1", "tenant_id": str(tid),
                                    "user_id": str(uid)}  # fmt: skip
        assert ctx.product == "claims"
    assert current() is None


def _scope(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "http", "method": "GET", "path": "/x", "headers": [],
                            "client": ("10.0.0.5", 1234)}  # fmt: skip
    base.update(kw)
    return base


async def _run(mw: RequestContextMiddleware, scope: dict[str, Any]) -> tuple[list[Any], Any]:
    seen: dict[str, Any] = {}
    sent: list[Any] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: Any) -> None:  # noqa: ANN401
        sent.append(message)

    async def app(scope: Any, receive: Any, send: Any) -> None:  # noqa: ANN401
        seen["ctx"] = current()
        scope["route"] = type("R", (), {"path_format": "/items/{item_id}"})()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw.app = app
    await mw(scope, receive, send)
    return sent, seen["ctx"]


@pytest.mark.anyio
async def test_middleware_sets_context_and_header() -> None:
    mw = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
    scope = _scope(headers=[(b"x-request-id", b"req-42"), (b"user-agent", b"pytest-agent"),
                            (b"x-forwarded-for", b"203.0.113.9")])  # fmt: skip
    sent, ctx = await _run(mw, scope)
    assert ctx.request_id == "req-42" and ctx.ip == "10.0.0.5"
    assert ctx.user_agent == "pytest-agent" and ctx.route == "/items/{item_id}"
    assert (b"x-request-id", b"req-42") in sent[0]["headers"]
    assert current() is None  # reset after the request


@pytest.mark.anyio
async def test_middleware_trusts_forwarded_for_only_from_trusted_proxies() -> None:
    proxies = context.parse_proxy_cidrs("10.0.0.0/8")
    mw = RequestContextMiddleware(app=None, trusted_proxies=proxies)  # type: ignore[arg-type]
    # _scope's peer is 10.0.0.5, inside the trusted range
    _, ctx = await _run(mw, _scope(headers=[(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")]))
    assert ctx.ip == "203.0.113.9"  # the hop the proxy appended, not the spoofed one


def _client_scope(peer: str, xff: str | None = None) -> dict[str, Any]:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return {"type": "http", "client": (peer, 1234), "headers": headers}


@pytest.mark.parametrize(
    ("peer", "xff", "expected"),
    [
        # no trusted proxies configured: the header is never read
        ("198.51.100.7", "203.0.113.9", "198.51.100.7"),
    ],
)
def test_client_ip_without_trusted_proxies(peer: str, xff: str, expected: str) -> None:
    assert context.client_ip(_client_scope(peer, xff)) == expected


@pytest.mark.parametrize(
    ("peer", "xff", "expected"),
    [
        # a visitor talking to us directly can't choose their IP
        ("198.51.100.7", "203.0.113.9", "198.51.100.7"),
        # through the proxy: the proxy appended the real client on the right
        ("10.1.2.3", "203.0.113.9", "203.0.113.9"),
        # a visitor's own header is to the left of what the proxy appended
        ("10.1.2.3", "1.2.3.4, 203.0.113.9", "203.0.113.9"),
        # two trusted proxy hops are skipped
        ("10.1.2.3", "203.0.113.9, 10.9.9.9", "203.0.113.9"),
        # IPv6 client and proxy
        ("fd00::1", "2001:db8::7", "2001:db8::7"),
        # malformed hop: fall back to the peer
        ("10.1.2.3", "1.2.3.4, not-an-ip", "10.1.2.3"),
        # empty header: the peer
        ("10.1.2.3", " , ", "10.1.2.3"),
        # every hop is a trusted proxy: the left-most one
        ("10.1.2.3", "10.0.0.8, 10.0.0.9", "10.0.0.8"),
        # a non-IP peer (e.g. a unix socket) is never trusted
        ("testclient", "203.0.113.9", "testclient"),
    ],
)
def test_client_ip_with_trusted_proxies(peer: str, xff: str, expected: str) -> None:
    proxies = context.parse_proxy_cidrs("10.0.0.0/8, fd00::/8")
    assert context.client_ip(_client_scope(peer, xff), proxies) == expected


def test_client_ip_without_header_or_client() -> None:
    proxies = context.parse_proxy_cidrs(["10.0.0.0/8"])
    assert context.client_ip(_client_scope("10.1.2.3"), proxies) == "10.1.2.3"
    assert context.client_ip({"type": "http", "headers": []}, proxies) is None


def test_parse_proxy_cidrs() -> None:
    assert context.parse_proxy_cidrs(None) == ()
    assert [str(n) for n in context.parse_proxy_cidrs("10.0.0.1/8 192.0.2.0/24,")] == [
        "10.0.0.0/8", "192.0.2.0/24"]  # fmt: skip
    with pytest.raises(ValueError):
        context.parse_proxy_cidrs("not-a-cidr")


@pytest.mark.anyio
async def test_middleware_passes_non_http_through() -> None:
    called: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:  # noqa: ANN401
        called.append(scope["type"])

    mw = RequestContextMiddleware(app)
    await mw({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert called == ["lifespan"]


def test_ensure_request_context_is_idempotent_and_outermost() -> None:
    from fastapi import FastAPI
    from starlette.middleware.gzip import GZipMiddleware

    app = FastAPI()
    ensure_request_context(app)
    app.add_middleware(GZipMiddleware)
    ensure_request_context(app, trusted_proxies=context.parse_proxy_cidrs("10.0.0.0/8"))
    classes: list[object] = [getattr(m, "cls", None) for m in app.user_middleware]
    assert classes.count(context.RequestContextMiddleware) == 1
    assert classes[0] is context.RequestContextMiddleware  # first = outermost
