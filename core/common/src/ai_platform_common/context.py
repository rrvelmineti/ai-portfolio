"""Per-request context shared by logging, auth, the gateway and observability.

One mutable ``RequestContext`` object lives in a ``ContextVar`` for the duration of a
request (or a job/script run). Code further down the stack fills in what it learns,
e.g. auth sets ``tenant_id`` and ``user_id``. Because the object is shared (not
copied), outer middleware such as the access log and the audit writer see those
values after the response.

    from ai_platform_common.context import current, request_context

    with request_context(product="claims-onboarding"):   # scripts / background jobs
        complete("...")                                   # picks up the request_id

In web apps ``RequestContextMiddleware`` (pure ASGI) creates the context.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


@dataclass
class RequestContext:
    """What is known about the current request or job."""

    request_id: str
    method: str | None = None
    path: str | None = None
    route: str | None = None  # path template, e.g. /auth/users/{user_id}
    ip: str | None = None
    user_agent: str | None = None
    tenant_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    product: str | None = None
    audited: bool = False  # an explicit audit row was written for this request
    started_at: float = field(default_factory=time.perf_counter)

    def log_fields(self) -> dict[str, str | None]:
        """Fields every log line carries."""
        return {
            "request_id": self.request_id,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
        }


_current: ContextVar[RequestContext | None] = ContextVar("ai_platform_request", default=None)


def new_request_id() -> str:
    return str(uuid.uuid4())


def clean_request_id(incoming: str | None) -> str:
    """Accept a caller's request id only if it is short and safe; otherwise make one."""
    if incoming and _SAFE_ID.match(incoming):
        return incoming
    return new_request_id()


def current() -> RequestContext | None:
    """The active context, if any."""
    return _current.get()


def current_request_id() -> str | None:
    ctx = _current.get()
    return ctx.request_id if ctx else None


def set_user(tenant_id: uuid.UUID | None, user_id: uuid.UUID | None) -> None:
    """Record the authenticated user on the active context (no-op outside one)."""
    ctx = _current.get()
    if ctx is not None:
        ctx.tenant_id = tenant_id
        ctx.user_id = user_id


@contextmanager
def request_context(request_id: str | None = None, **fields: Any) -> Iterator[RequestContext]:  # noqa: ANN401
    """Open a context for code that runs outside an HTTP request (scripts, jobs, tests)."""
    ctx = RequestContext(request_id=clean_request_id(request_id), **fields)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


# ---------------------------------------------------------------- ASGI middleware
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return bytes(value).decode("latin-1")
    return None


def client_ip(scope: Scope, trust_proxy_headers: bool) -> str | None:
    """Client IP; ``X-Forwarded-For`` only when explicitly trusted."""
    if trust_proxy_headers:
        forwarded = _header(scope, b"x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    client = scope.get("client")
    return str(client[0]) if client else None


def route_template(scope: Scope) -> str | None:
    """The matched route's path template (set by Starlette after routing)."""
    route = scope.get("route")
    path = getattr(route, "path_format", None) or getattr(route, "path", None)
    return str(path) if path else None


class RequestContextMiddleware:
    """Pure ASGI middleware: create the context, echo ``X-Request-ID``.

    Pure ASGI (not ``BaseHTTPMiddleware``) so the endpoint runs in the same context and
    values set downstream (user, tenant) are visible to outer middleware afterwards.
    """

    def __init__(self, app: ASGIApp, trust_proxy_headers: bool = False) -> None:
        self.app = app
        self.trust_proxy_headers = trust_proxy_headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ctx = RequestContext(
            request_id=clean_request_id(_header(scope, b"x-request-id")),
            method=scope.get("method"),
            path=scope.get("path"),
            ip=client_ip(scope, self.trust_proxy_headers),
            user_agent=(_header(scope, b"user-agent") or "")[:500] or None,
        )
        token = _current.set(ctx)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), ctx.request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            ctx.route = ctx.route or route_template(scope)
            _current.reset(token)


def ensure_request_context(app: Any, *, trust_proxy_headers: bool = False) -> None:  # noqa: ANN401
    """Add ``RequestContextMiddleware`` as the outermost middleware, exactly once.

    Safe to call from several installers (auth, observability) in any order.
    """
    app.user_middleware = [
        m for m in app.user_middleware if getattr(m, "cls", None) is not RequestContextMiddleware
    ]
    app.add_middleware(RequestContextMiddleware, trust_proxy_headers=trust_proxy_headers)
