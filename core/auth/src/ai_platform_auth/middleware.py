"""Audit middleware: one audit row for every write request not audited explicitly.

Pure ASGI, so it runs in the same request context as the endpoint and sees the
authenticated user and the ``audited`` flag set downstream. The request id itself
comes from ``ai_platform_common.context.RequestContextMiddleware``.
"""

from __future__ import annotations

from ai_platform_common.context import ASGIApp, Message, Receive, Scope, Send, current
from ai_platform_common.logging import get_logger
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from ai_platform_auth.audit import audit

log = get_logger("ai_platform_auth.middleware")

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AuditMiddleware:
    """Audit POST/PUT/PATCH/DELETE requests that no endpoint audited itself.

    Endpoints with their own audit entries (login, user changes, ...) set
    ``RequestContext.audited``; everything else gets one generic ``http.<method>`` row.
    An audit failure is logged and never changes the response.
    """

    def __init__(self, app: ASGIApp, session_factory: sessionmaker[Session]) -> None:
        self.app = app
        self.session_factory = session_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in WRITE_METHODS:
            await self.app(scope, receive, send)
            return
        status = 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            ctx = current()
            if ctx is None or not ctx.audited:
                await run_in_threadpool(self._write, str(scope["method"]), str(scope["path"]),
                                        status)  # fmt: skip

    def _write(self, method: str, path: str, status: int) -> None:
        ctx = current()
        try:
            with self.session_factory() as session:
                audit(
                    session, f"http.{method.lower()}", resource_type="http",
                    resource_id=path[:200], details={"status": status},
                )  # fmt: skip
                session.commit()
        except Exception:  # auditing must never turn a response into a 500
            log.exception(
                "audit.write_failed",
                extra={"fields": {"request_id": ctx.request_id if ctx else None}},
            )
