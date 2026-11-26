"""The content redaction hook. Phase 0.5 (Presidio) plugs in here.

Every piece of prompt/response text leaves the process only via ``redact_content``.
Until a redactor is registered it returns the content unchanged; content is only
captured at all when ``LANGSMITH_LOG_CONTENT=true``.

    from ai_platform_observability.redaction import set_content_redactor

    set_content_redactor(presidio_redactor)   # Phase 0.5
"""

from __future__ import annotations

from collections.abc import Callable

from ai_platform_gateway.hooks import CallContent

ContentRedactor = Callable[[CallContent], CallContent]


def _identity(content: CallContent) -> CallContent:
    return content


_redactor: dict[str, ContentRedactor] = {"fn": _identity}


def set_content_redactor(fn: ContentRedactor | None) -> None:
    """Register the redactor applied to all outgoing content (``None`` resets it)."""
    _redactor["fn"] = fn or _identity


def redact_content(content: CallContent) -> CallContent:
    """Apply the registered redactor. Always call this before sending content anywhere."""
    return _redactor["fn"](content)
