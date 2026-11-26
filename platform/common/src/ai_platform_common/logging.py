"""Structured JSON logging shared by the platform and products.

Usage::

    from ai_platform_common.logging import get_logger

    log = get_logger(__name__)
    log.info("gateway.call", extra={"fields": {"request_id": "...", "status": "ok"}})

Every record is rendered as a single JSON object per line. Structured fields are
passed under ``extra={"fields": {...}}`` so they never collide with stdlib
``LogRecord`` attributes.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

_CONFIGURED_ATTR = "_ai_platform_json_handler"


class JsonFormatter(logging.Formatter):
    """Render log records as one-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str | None = None) -> None:
    """Install a JSON stream handler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel((level or os.environ.get("LOG_LEVEL") or "INFO").upper())
    for handler in root.handlers:
        if getattr(handler, _CONFIGURED_ATTR, False):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _CONFIGURED_ATTR, True)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a standard library logger; call ``configure_logging`` once at startup."""
    return logging.getLogger(name)
