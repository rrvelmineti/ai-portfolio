"""Structured JSON logging shared by the platform and products.

Call ``configure_logging()`` once at startup. Every line is one JSON object with
``timestamp``, ``level``, ``logger``, ``message``, the request context
(``request_id``, ``tenant_id``, ``user_id``) and any structured fields::

    log = get_logger(__name__)
    log.info("claim.saved", extra={"fields": {"claim_id": "c-1", "fields_found": 7}})

Structured fields go under ``extra={"fields": {...}}`` so they never collide with
stdlib ``LogRecord`` attributes. Values under sensitive keys (password, token, secret,
authorization, api_key, cookie, ...) are replaced with ``[redacted]`` at any depth.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ai_platform_common.context import current

_CONFIGURED_ATTR = "_ai_platform_json_handler"
REDACTED = "[redacted]"

# A key is sensitive when any of its parts (split on _ - . and spaces) is one of these...
_SENSITIVE_PARTS = frozenset(
    {"password", "passwd", "pwd", "token", "secret", "authorization", "cookie", "cookies", "apikey",
     "jwt", "dsn", "credential", "credentials", "passphrase"}
)  # fmt: skip
# ...or when it contains one of these two-part names.
_SENSITIVE_PAIRS = ("api_key", "private_key", "access_key", "session_id")
_SPLIT = re.compile(r"[-.\s]+")


def is_sensitive_key(key: str) -> bool:
    """True for keys like ``password``, ``access_token``, ``X-API-Key``, ``Set-Cookie``.

    Counts such as ``input_tokens`` are *not* sensitive (the part is ``tokens``).
    """
    norm = _SPLIT.sub("_", key.strip().lower())
    parts = set(norm.split("_"))
    return bool(parts & _SENSITIVE_PARTS) or any(p in norm for p in _SENSITIVE_PAIRS)


def redact(value: Any) -> Any:  # noqa: ANN401 - works on any JSON-like structure
    """Return a copy with values under sensitive keys replaced, at any depth."""
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and is_sensitive_key(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        # header lists like [("authorization", "Bearer ...")]
        if all(isinstance(i, list | tuple) and len(i) == 2 for i in value) and value:
            return [
                (k, REDACTED) if isinstance(k, str | bytes) and is_sensitive_key(
                    k.decode("latin-1") if isinstance(k, bytes) else k) else (k, redact(v))
                for k, v in value
            ]  # fmt: skip
        return [redact(v) for v in value]
    return value


class RedactionFilter(logging.Filter):
    """Redact sensitive keys in a record's structured ``fields`` (and dict args)."""

    def filter(self, record: logging.LogRecord) -> bool:
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            record.fields = redact(fields)
        if isinstance(record.args, Mapping):
            record.args = redact(record.args)
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as one-line JSON objects (redacting sensitive keys)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        stamped = getattr(record, "context_fields", None)
        if isinstance(stamped, dict):
            payload.update({k: v for k, v in stamped.items() if v is not None})
        else:
            ctx = current()
            if ctx is not None:
                payload.update({k: v for k, v in ctx.log_fields().items() if v is not None})
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload.update(redact(fields))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


# Third-party loggers routed through our handler. uvicorn's own access log is replaced
# by the observability access line (one per request, with the route template).
_ROUTED = ("uvicorn", "uvicorn.error", "fastapi", "sqlalchemy", "alembic")
_QUIET = {"sqlalchemy.engine": logging.WARNING, "sqlalchemy.pool": logging.WARNING,
          "httpx": logging.WARNING, "httpx2": logging.WARNING,
          # presidio-analyzer DEBUG lines contain the analysed text's context words (PII)
          "presidio-analyzer": logging.WARNING, "presidio-anonymizer": logging.WARNING}  # fmt: skip


def _install_context_record_factory() -> None:
    """Stamp the request context onto each record when it is *created*, so handlers
    that format later (queues, test capture) still see the right request_id."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, _CONFIGURED_ATTR, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:  # noqa: ANN401
        record = previous(*args, **kwargs)
        ctx = current()
        if ctx is not None:
            record.context_fields = ctx.log_fields()
        return record

    setattr(factory, _CONFIGURED_ATTR, True)
    logging.setLogRecordFactory(factory)


def configure_logging(level: str | None = None) -> None:
    """Install one JSON handler on the root logger and route framework loggers to it.

    Idempotent. SQLAlchemy engine logs are capped at WARNING so SQL parameters are
    never written. uvicorn's access logger is silenced (we log our own access line).
    """
    root = logging.getLogger()
    root.setLevel((level or os.environ.get("LOG_LEVEL") or "INFO").upper())
    _install_context_record_factory()
    if not any(getattr(h, _CONFIGURED_ATTR, False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactionFilter())
        setattr(handler, _CONFIGURED_ATTR, True)
        root.addHandler(handler)
    for name in _ROUTED:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name, lvl in _QUIET.items():
        logging.getLogger(name).setLevel(lvl)


def get_logger(name: str) -> logging.Logger:
    """Return a standard library logger; call ``configure_logging`` once at startup."""
    return logging.getLogger(name)
