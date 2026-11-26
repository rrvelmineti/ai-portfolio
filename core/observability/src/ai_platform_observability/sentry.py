"""Optional Sentry error reporting. Initialised only when ``SENTRY_DSN`` is set."""

from __future__ import annotations

from typing import Any

import sentry_sdk
from ai_platform_common.logging import get_logger, redact

from ai_platform_observability.settings import ObservabilitySettings

log = get_logger("ai_platform_observability.sentry")


def scrub_event(event: Any, _hint: Any = None) -> Any:  # noqa: ANN401 - Sentry's own types
    """``before_send``: drop sensitive keys (headers, cookies, body fields, extras)."""
    return redact(event)


def scrub_breadcrumb(crumb: Any, _hint: Any = None) -> Any:  # noqa: ANN401
    """``before_breadcrumb``: same redaction for breadcrumbs."""
    return redact(crumb)


def init_sentry(settings: ObservabilitySettings) -> bool:
    """Initialise Sentry if a DSN is configured. Returns whether it was initialised."""
    if not settings.sentry_enabled:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        # sentry-sdk 3.x replaces send_default_pii with data_collection; this is the
        # "no PII" setting: no user identity, cookies, bodies, AI inputs/outputs or
        # frame variables; headers and query params filtered by Sentry's denylist.
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "denylist"}},
            "http_bodies": [],
            "url_query_params": {"mode": "denylist"},
            "gen_ai": {"inputs": False, "outputs": False},
            "mcp": {"inputs": False, "outputs": False},
            "graphql": {"document": False, "variables": False},
            "database_query_data": False,
            "stack_frame_variables": False,
        },
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        traces_sample_rate=0.0,  # errors only; tracing goes to LangSmith/metrics
    )
    log.info("sentry.initialised", extra={"fields": {"environment": settings.sentry_environment}})
    return True
