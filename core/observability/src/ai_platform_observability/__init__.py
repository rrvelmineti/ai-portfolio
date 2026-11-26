"""Observability for ai-portfolio: request context wiring, logs, AI call tracing, metrics.

from ai_platform_observability import install_observability, traced

obs = install_observability(app, session_factory, product="claims-onboarding")

with traced("map file", metadata={"file_id": file_id}):
    ...
"""

from ai_platform_observability.install import (
    Observability,
    install_observability,
    setup_tracing,
)
from ai_platform_observability.langsmith_tracer import LangSmithTracer, traced
from ai_platform_observability.metrics import Metrics
from ai_platform_observability.recorder import LlmCallRecorder
from ai_platform_observability.redaction import redact_content, set_content_redactor
from ai_platform_observability.settings import ObservabilitySettings

__all__ = [
    "LangSmithTracer",
    "LlmCallRecorder",
    "Metrics",
    "Observability",
    "ObservabilitySettings",
    "install_observability",
    "redact_content",
    "set_content_redactor",
    "setup_tracing",
    "traced",
]
