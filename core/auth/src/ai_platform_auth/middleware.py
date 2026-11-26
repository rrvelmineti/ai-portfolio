"""Request-ID and audit middleware."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from ai_platform_common.logging import get_logger
from fastapi import Request, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ai_platform_auth.audit import RequestInfo, audit

log = get_logger("ai_platform_auth.middleware")

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def client_ip(request: Request, trust_proxy_headers: bool) -> str | None:
    """Client IP. ``X-Forwarded-For`` is used only when explicitly trusted."""
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def request_info(request: Request) -> RequestInfo:
    """The ``RequestInfo`` attached by ``RequestContextMiddleware`` (or a fresh one)."""
    info = getattr(request.state, "request_info", None)
    if isinstance(info, RequestInfo):
        return info
    info = RequestInfo(request_id=str(uuid.uuid4()))
    request.state.request_info = info
    return info


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Accept a safe incoming X-Request-ID or generate one; echo it on the response."""

    def __init__(self, app: ASGIApp, trust_proxy_headers: bool = False) -> None:
        super().__init__(app)
        self.trust_proxy_headers = trust_proxy_headers

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        rid = incoming if _SAFE_ID.match(incoming) else str(uuid.uuid4())
        request.state.request_info = RequestInfo(
            request_id=rid,
            ip=client_ip(request, self.trust_proxy_headers),
            user_agent=request.headers.get("user-agent"),
        )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


class AuditMiddleware(BaseHTTPMiddleware):
    """Audit every write request that no endpoint audited explicitly.

    Endpoints with their own audit entries (login, user changes, ...) set
    ``RequestInfo.audited``; everything else gets one generic ``http.<method>`` row.
    """

    def __init__(self, app: ASGIApp, session_factory: sessionmaker[Session]) -> None:
        super().__init__(app)
        self.session_factory = session_factory

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if request.method in WRITE_METHODS:
            info = request_info(request)
            if not info.audited:
                await run_in_threadpool(self._write, request, info, response.status_code)
        return response

    def _write(self, request: Request, info: RequestInfo, status: int) -> None:
        tenant_id = getattr(request.state, "auth_tenant_id", None)
        user_id = getattr(request.state, "auth_user_id", None)
        try:
            with self.session_factory() as session:
                audit(
                    session, f"http.{request.method.lower()}", request=info,
                    tenant_id=tenant_id, user_id=user_id, resource_type="http",
                    resource_id=request.url.path[:200], details={"status": status},
                )  # fmt: skip
                session.commit()
        except Exception:  # auditing must never turn a response into a 500
            log.exception("audit.write_failed", extra={"fields": {"request_id": info.request_id}})
