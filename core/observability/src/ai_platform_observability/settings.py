"""Observability settings. Every integration is off when its env vars are missing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ai_platform_common.config import get_bool, get_env

DEFAULT_LANGSMITH_PROJECT = "ai-portfolio-dev"


@dataclass(frozen=True)
class ObservabilitySettings:
    """``repr`` never shows the LangSmith key or the Sentry DSN."""

    langsmith_api_key: str | None = field(default=None, repr=False)
    langsmith_project: str = DEFAULT_LANGSMITH_PROJECT
    langsmith_endpoint: str | None = None
    langsmith_log_content: bool = False
    sentry_dsn: str | None = field(default=None, repr=False)
    sentry_environment: str | None = None

    @property
    def langsmith_enabled(self) -> bool:
        return self.langsmith_api_key is not None

    @property
    def sentry_enabled(self) -> bool:
        return self.sentry_dsn is not None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ObservabilitySettings:
        return cls(
            langsmith_api_key=get_env("LANGSMITH_API_KEY", environ),
            langsmith_project=get_env("LANGSMITH_PROJECT", environ) or DEFAULT_LANGSMITH_PROJECT,
            langsmith_endpoint=get_env("LANGSMITH_ENDPOINT", environ),
            langsmith_log_content=get_bool("LANGSMITH_LOG_CONTENT", False, environ),
            sentry_dsn=get_env("SENTRY_DSN", environ),
            sentry_environment=get_env("APP_ENV", environ),
        )
