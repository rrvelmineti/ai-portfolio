"""``install_observability(app, ...)``: wire everything in one call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_platform_common.context import ensure_request_context
from ai_platform_common.logging import configure_logging
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_observability.http import ObservabilityMiddleware, build_ops_router
from ai_platform_observability.langsmith_tracer import LangSmithTracer, set_tracer
from ai_platform_observability.metrics import Metrics
from ai_platform_observability.recorder import LlmCallRecorder, register_recorder
from ai_platform_observability.sentry import init_sentry
from ai_platform_observability.settings import ObservabilitySettings


@dataclass
class Observability:
    """Handles to what ``install_observability`` set up (useful in tests and scripts)."""

    settings: ObservabilitySettings
    metrics: Metrics
    recorder: LlmCallRecorder
    tracer: LangSmithTracer | None
    sentry_enabled: bool


def setup_tracing(
    session_factory: sessionmaker[Session] | None = None,
    *,
    settings: ObservabilitySettings | None = None,
    metrics: Metrics | None = None,
    tracer: LangSmithTracer | None = None,
) -> Observability:
    """Register the AI-call recorder without a web app (scripts, workers)."""
    settings = settings or ObservabilitySettings.from_env()
    metrics = metrics or Metrics()
    tracer = tracer if tracer is not None else LangSmithTracer.from_settings(settings)
    set_tracer(tracer)
    recorder = register_recorder(LlmCallRecorder(session_factory, metrics, tracer))
    return Observability(settings, metrics, recorder, tracer, sentry_enabled=False)


def install_observability(
    app: Any,  # noqa: ANN401 - a FastAPI/Starlette app
    session_factory: sessionmaker[Session] | None = None,
    *,
    product: str | None = None,
    settings: ObservabilitySettings | None = None,
    metrics: Metrics | None = None,
    tracer: LangSmithTracer | None = None,
    trust_proxy_headers: bool = False,
) -> Observability:
    """Logging, request context, access log, metrics, health, AI-call tracing, Sentry.

    Every integration is a no-op when its env vars are missing (LangSmith, Sentry).
    """
    configure_logging()
    obs = setup_tracing(session_factory, settings=settings, metrics=metrics, tracer=tracer)
    app.include_router(build_ops_router(obs.metrics, session_factory))
    app.add_middleware(ObservabilityMiddleware, metrics=obs.metrics, product=product)
    ensure_request_context(app, trust_proxy_headers=trust_proxy_headers)  # stays outermost
    obs.sentry_enabled = init_sentry(obs.settings)
    return obs
