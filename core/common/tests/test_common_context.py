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
async def test_middleware_trusts_forwarded_for_only_when_enabled() -> None:
    mw = RequestContextMiddleware(app=None, trust_proxy_headers=True)  # type: ignore[arg-type]
    _, ctx = await _run(mw, _scope(headers=[(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")]))
    assert ctx.ip == "203.0.113.9"


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
    ensure_request_context(app, trust_proxy_headers=True)
    classes: list[object] = [getattr(m, "cls", None) for m in app.user_middleware]
    assert classes.count(context.RequestContextMiddleware) == 1
    assert classes[0] is context.RequestContextMiddleware  # first = outermost
