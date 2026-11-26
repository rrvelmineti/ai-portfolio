"""Prometheus metrics for HTTP requests and AI calls (own registry, no globals)."""

from __future__ import annotations

from ai_platform_gateway.hooks import CallRecord
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_LLM_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)

CONTENT_TYPE = CONTENT_TYPE_LATEST


class Metrics:
    """All metrics live in one registry so apps and tests don't collide."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.http_requests = Counter(
            "http_requests_total", "HTTP requests", ["method", "route", "status"], registry=r
        )
        self.http_latency = Histogram(
            "http_request_duration_seconds", "HTTP request latency", ["method", "route"],
            buckets=_HTTP_BUCKETS, registry=r,
        )  # fmt: skip
        self.llm_calls = Counter(
            "llm_calls_total", "AI calls", ["provider", "model", "status"], registry=r
        )
        self.llm_tokens = Counter(
            "llm_tokens_total", "AI tokens", ["provider", "model", "direction"], registry=r
        )
        self.llm_cost = Counter(
            "llm_cost_usd_total", "AI cost in USD (known prices only)", ["provider", "model"],
            registry=r,
        )  # fmt: skip
        self.llm_cost_unknown = Counter(
            "llm_cost_unknown_total", "AI calls without a known price", ["provider", "model"],
            registry=r,
        )  # fmt: skip
        self.llm_latency = Histogram(
            "llm_call_duration_seconds", "AI call latency incl. retries/fallback",
            ["provider", "model"], buckets=_LLM_BUCKETS, registry=r,
        )  # fmt: skip
        self.llm_fallbacks = Counter(
            "llm_fallbacks_total", "AI calls answered by a fallback entry", ["alias"], registry=r
        )

    def observe_http(self, method: str, route: str, status: int, seconds: float) -> None:
        self.http_requests.labels(method, route, str(status)).inc()
        self.http_latency.labels(method, route).observe(seconds)

    def observe_llm(self, record: CallRecord) -> None:
        provider = record.provider or "none"
        model = record.model or "none"
        self.llm_calls.labels(provider, model, record.status).inc()
        if record.input_tokens:
            self.llm_tokens.labels(provider, model, "input").inc(record.input_tokens)
        if record.output_tokens:
            self.llm_tokens.labels(provider, model, "output").inc(record.output_tokens)
        if record.cost_usd is not None:
            self.llm_cost.labels(provider, model).inc(record.cost_usd)
        elif record.status == "ok":
            self.llm_cost_unknown.labels(provider, model).inc()
        self.llm_latency.labels(provider, model).observe(record.latency_ms / 1000)
        if record.fallback_used:
            self.llm_fallbacks.labels(record.alias).inc()

    def render(self) -> bytes:
        """Prometheus text exposition format."""
        return generate_latest(self.registry)
