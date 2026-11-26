"""HTTP side: access log + metrics middleware, /metrics, /healthz, /readyz.

No ``from __future__ import annotations``: FastAPI inspects the endpoint signatures.
"""

import time

from ai_platform_common.context import (
    ASGIApp,
    Message,
    Receive,
    Scope,
    Send,
    current,
    route_template,
)
from ai_platform_common.logging import get_logger
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from ai_platform_observability.metrics import CONTENT_TYPE, Metrics

log = get_logger("ai_platform_observability.access")

QUIET_ROUTES = frozenset({"/metrics", "/healthz", "/readyz"})


class ObservabilityMiddleware:
    """One access-log line and HTTP metrics per request (pure ASGI).

    Uses the route *template* (``/auth/users/{user_id}``), never the raw path, so IDs
    don't leak into logs or explode metric cardinality. Must run inside
    ``RequestContextMiddleware``.
    """

    def __init__(self, app: ASGIApp, metrics: Metrics, product: str | None = None) -> None:
        self.app = app
        self.metrics = metrics
        self.product = product

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ctx = current()
        if ctx is not None and self.product and ctx.product is None:
            ctx.product = self.product
        start = time.perf_counter()
        status = 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            elapsed = time.perf_counter() - start
            method = str(scope.get("method", ""))
            route = route_template(scope) or "unmatched"
            if ctx is not None:
                ctx.route = route
            self.metrics.observe_http(method, route, status, elapsed)
            level = log.debug if route in QUIET_ROUTES else log.info
            level(
                "http.access",
                extra={"fields": {"method": method, "route": route, "status": status,
                                  "latency_ms": round(elapsed * 1000, 2),
                                  "product": ctx.product if ctx else self.product}},
            )  # fmt: skip


def check_database(session_factory: sessionmaker[Session]) -> None:
    """Raise if the database cannot answer ``SELECT 1``."""
    with session_factory() as session:
        session.execute(text("SELECT 1"))


def build_ops_router(metrics: Metrics, session_factory: sessionmaker[Session] | None) -> APIRouter:
    """``/metrics``, ``/healthz`` and ``/readyz`` (unauthenticated, for scrapers/probes)."""
    router = APIRouter(tags=["ops"])

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        """The process is up."""
        return {"status": "ok"}

    @router.get("/readyz", response_model=None)
    async def readyz() -> JSONResponse:
        """Ready to serve: the database answers. 503 otherwise."""
        if session_factory is None:
            return JSONResponse({"status": "ok", "database": "not configured"})
        try:
            await run_in_threadpool(check_database, session_factory)
        except Exception as exc:
            log.warning("readyz.database_unreachable",
                        extra={"fields": {"error_type": type(exc).__name__}})  # fmt: skip
            return JSONResponse({"status": "unavailable", "database": "unreachable"},
                                status_code=503)  # fmt: skip
        return JSONResponse({"status": "ok", "database": "ok"})

    @router.get("/metrics")
    def prometheus_metrics() -> Response:
        """Prometheus scrape endpoint."""
        return Response(metrics.render(), media_type=CONTENT_TYPE)

    return router
