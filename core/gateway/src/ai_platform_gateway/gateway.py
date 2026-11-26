"""Routing, fallback, retries, structured output, cost and the per-call log line."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, Literal, TypeVar

from ai_platform_common.config import debug_log_prompts
from ai_platform_common.context import current, current_request_id
from ai_platform_common.logging import get_logger
from pydantic import BaseModel, ValidationError

from ai_platform_gateway import hooks, structured
from ai_platform_gateway.config import AliasConfig, GatewayConfig, ModelEntry, default_config
from ai_platform_gateway.cost import compute_cost
from ai_platform_gateway.errors import (
    AllProvidersFailed,
    GatewayError,
    InvalidRequest,
    NoProvidersConfigured,
    NotSupported,
    SchemaValidationError,
)
from ai_platform_gateway.providers.base import (
    CompletionRequest,
    EmbeddingRequest,
    Provider,
    ProviderCompletion,
    ProviderEmbedding,
)
from ai_platform_gateway.providers.registry import ProviderRegistry
from ai_platform_gateway.retry import RetryPolicy
from ai_platform_gateway.types import (
    AttemptRecord,
    CompletionResult,
    EmbeddingResult,
    Message,
    TextPart,
)

log = get_logger("ai_platform_gateway")

T = TypeVar("T")


class _ChainOutcome[T]:
    """Result of walking one alias's fallback chain."""

    def __init__(
        self, entry: ModelEntry, model_id: str, value: T, records: list[AttemptRecord]
    ) -> None:
        self.entry = entry
        self.model_id = model_id
        self.value = value
        self.records = records

    @property
    def fallback_used(self) -> bool:
        """True when the answer did not come from the first entry of the chain."""
        return len(self.records) > 1

    @property
    def attempts(self) -> int:
        return sum(r.attempts for r in self.records)


class _ChainError(Exception):
    """Internal: carries attempt records alongside the error to raise."""

    def __init__(self, error: GatewayError, records: list[AttemptRecord]) -> None:
        super().__init__(str(error))
        self.error = error
        self.records = records


class Gateway:
    """The model gateway. Most code uses the module-level ``complete``/``embed``."""

    def __init__(
        self,
        config: GatewayConfig | None = None,
        registry: ProviderRegistry | None = None,
        retry_policy: RetryPolicy | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config or default_config()
        self.registry = registry or ProviderRegistry()
        self.retry_policy = retry_policy or RetryPolicy()
        self.environ = environ  # None means os.environ, read at call time

    # ------------------------------------------------------------------ public API
    def complete(
        self,
        prompt: str | list[Message],
        model: str = "default",
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        response_schema: type[BaseModel] | None = None,
        timeout_s: float = 30,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> CompletionResult:
        """Run a chat completion through ``model``'s fallback chain."""
        rid = request_id or current_request_id() or str(uuid.uuid4())
        start = time.perf_counter()
        messages = [Message(role="user", content=prompt)] if isinstance(prompt, str) else prompt
        records: list[AttemptRecord] = []
        try:
            self._check_alias(model, "chat")
            if not messages:
                raise InvalidRequest("prompt must not be empty")
            full_system = structured.merge_system(system, response_schema)
            outcome = self._run_completion(
                model, messages, full_system, max_tokens, temperature, timeout_s,
                json_mode=response_schema is not None,
            )  # fmt: skip
            records.extend(outcome.records)
            fallback_used = outcome.fallback_used
            resp = outcome.value
            in_tok, out_tok = resp.input_tokens, resp.output_tokens
            cost = compute_cost(outcome.entry, in_tok, out_tok)
            parsed: BaseModel | None = None
            if response_schema is not None:
                try:
                    parsed = structured.parse(resp.text, response_schema)
                except ValidationError as first_error:
                    retry_messages = [
                        *messages,
                        Message(role="assistant", content=resp.text or "{}"),
                        Message(role="user", content=structured.repair_prompt(first_error)),
                    ]
                    outcome = self._run_completion(
                        model, retry_messages, full_system, max_tokens, temperature,
                        timeout_s, json_mode=True,
                    )  # fmt: skip
                    records.extend(outcome.records)
                    fallback_used = fallback_used or outcome.fallback_used
                    resp = outcome.value
                    in_tok += resp.input_tokens
                    out_tok += resp.output_tokens
                    second = compute_cost(outcome.entry, resp.input_tokens, resp.output_tokens)
                    cost = None if cost is None or second is None else round(cost + second, 10)
                    try:
                        parsed = structured.parse(resp.text, response_schema)
                    except ValidationError as second_error:
                        raise _ChainError(
                            SchemaValidationError(
                                "model output failed schema validation after one retry",
                                errors=str(second_error),
                                raw_text=resp.text,
                            ),
                            [],
                        ) from second_error
        except _ChainError as exc:
            records.extend(exc.records)
            self._finish("complete", rid, model, start, records, metadata, error=exc.error,
                         content=(system, messages, None))  # fmt: skip
            raise exc.error from exc.error.__cause__
        except GatewayError as exc:
            self._finish("complete", rid, model, start, records, metadata, error=exc,
                         content=(system, messages, None))  # fmt: skip
            raise

        latency = self._finish(
            "complete", rid, model, start, records, metadata,
            provider=outcome.entry.provider, model_id=outcome.model_id,
            input_tokens=in_tok, output_tokens=out_tok, cost=cost,
            fallback_used=fallback_used,
            content=(system, messages, resp.text),
        )  # fmt: skip
        return CompletionResult(
            text=resp.text,
            parsed=parsed,
            provider=outcome.entry.provider,
            model=outcome.model_id,
            alias=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=latency,
            request_id=rid,
            fallback_used=fallback_used,
            attempts=sum(r.attempts for r in records),
            finish_reason=resp.finish_reason,
            attempt_records=records,
        )

    def embed(
        self,
        texts: str | list[str],
        model: str = "default-embed",
        *,
        timeout_s: float = 30,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> EmbeddingResult:
        """Embed one or more texts through ``model``'s fallback chain."""
        rid = request_id or current_request_id() or str(uuid.uuid4())
        start = time.perf_counter()
        items = [texts] if isinstance(texts, str) else list(texts)
        records: list[AttemptRecord] = []
        try:
            alias = self._check_alias(model, "embed")
            if not items:
                raise InvalidRequest("texts must not be empty")
            dims = alias.dimensions

            def call(provider: Provider, entry: ModelEntry, model_id: str) -> ProviderEmbedding:
                if dims and entry.supported_dimensions and dims not in entry.supported_dimensions:
                    raise NotSupported(f"cannot produce {dims}-dim vectors")
                out = provider.embed(EmbeddingRequest(model_id, items, dims, timeout_s))
                if len(out.vectors) != len(items):
                    raise NotSupported("provider returned the wrong number of vectors")
                if dims and any(len(v) != dims for v in out.vectors):
                    raise NotSupported(f"provider returned vectors that are not {dims}-dim")
                return out

            outcome = self._run_chain(model, call, needs_images=False)
            records = outcome.records
        except _ChainError as exc:
            self._finish("embed", rid, model, start, exc.records, metadata, error=exc.error,
                         content=(None, items, None))  # fmt: skip
            raise exc.error from exc.error.__cause__
        except GatewayError as exc:
            self._finish("embed", rid, model, start, records, metadata, error=exc,
                         content=(None, items, None))  # fmt: skip
            raise

        resp = outcome.value
        cost = compute_cost(outcome.entry, resp.tokens)
        latency = self._finish(
            "embed", rid, model, start, records, metadata,
            provider=outcome.entry.provider, model_id=outcome.model_id,
            input_tokens=resp.tokens, cost=cost, fallback_used=outcome.fallback_used,
            content=(None, items, None),
        )  # fmt: skip
        return EmbeddingResult(
            vectors=resp.vectors,
            provider=outcome.entry.provider,
            model=outcome.model_id,
            alias=model,
            dimensions=len(resp.vectors[0]) if resp.vectors else 0,
            tokens=resp.tokens,
            cost_usd=cost,
            latency_ms=latency,
            request_id=rid,
            fallback_used=outcome.fallback_used,
            attempts=outcome.attempts,
            attempt_records=records,
        )

    # ------------------------------------------------------------------ internals
    def _check_alias(self, alias: str, kind: str) -> AliasConfig:
        cfg = self.config.aliases.get(alias)
        if cfg is None:
            raise InvalidRequest(f"unknown model alias {alias!r}")
        if cfg.kind != kind:
            op = "embed()" if cfg.kind == "embed" else "complete()"
            raise InvalidRequest(f"alias {alias!r} is a {cfg.kind} alias; use it with {op}")
        return cfg

    def _run_completion(
        self,
        alias: str,
        messages: list[Message],
        system: str | None,
        max_tokens: int,
        temperature: float,
        timeout_s: float,
        *,
        json_mode: bool,
    ) -> _ChainOutcome[ProviderCompletion]:
        def call(provider: Provider, entry: ModelEntry, model_id: str) -> ProviderCompletion:
            temp: float | None = temperature
            if not entry.supports_temperature:
                temp = None
                log.debug(
                    "gateway.temperature_dropped",
                    extra={"fields": {"provider": entry.provider, "model": model_id}},
                )
            request = CompletionRequest(
                model=model_id,
                messages=messages,
                system=system,
                max_tokens=max_tokens,
                temperature=temp,
                timeout_s=timeout_s,
                json_mode=json_mode,
            )
            return provider.complete(request)

        needs_images = any(m.has_images() for m in messages)
        return self._run_chain(alias, call, needs_images=needs_images)

    def _run_chain(
        self,
        alias: str,
        call: Callable[[Provider, ModelEntry, str], T],
        *,
        needs_images: bool,
    ) -> _ChainOutcome[T]:
        chain = self.config.chain(alias)
        records: list[AttemptRecord] = []
        configured: list[tuple[ModelEntry, Provider]] = []
        missing_by_entry: dict[str, list[str]] = {}

        for entry in chain:
            provider = self.registry.get(entry.provider)
            missing_env = provider.missing_env(self.environ) + entry.missing_env(self.environ)
            if missing_env:
                missing_by_entry[entry.key or entry.provider] = missing_env
                log.warning(
                    "gateway.provider_skipped",
                    extra={"fields": {
                        "alias": alias, "provider": entry.provider,
                        "reason": "not_configured", "missing_env": missing_env,
                    }},
                )  # fmt: skip
                records.append(
                    AttemptRecord(
                        provider=entry.provider,
                        model=entry.model,
                        outcome="skipped",
                        error_type="NotConfigured",
                        reason="missing env: " + ", ".join(missing_env),
                    )
                )
                continue
            configured.append((entry, provider))

        if not configured:
            raise _ChainError(NoProvidersConfigured(alias, missing_by_entry), records)

        for entry, provider in configured:
            model_id = entry.resolved_model(self.environ)
            if needs_images and not entry.supports_images:
                records.append(
                    AttemptRecord(
                        provider=entry.provider,
                        model=model_id,
                        outcome="skipped",
                        error_type="NotSupported",
                        reason="model does not accept image input",
                    )
                )
                continue
            try:
                value, attempts = self.retry_policy.call(partial(call, provider, entry, model_id))
            except InvalidRequest as exc:
                records.append(self._failure(entry, model_id, exc, "failed"))
                raise _ChainError(exc, records) from exc
            except NotSupported as exc:
                records.append(self._failure(entry, model_id, exc, "skipped"))
                continue
            except GatewayError as exc:
                records.append(self._failure(entry, model_id, exc, "failed"))
                continue
            except Exception as exc:  # adapter bug or unmapped SDK error: fail over
                wrapped = GatewayError(f"unexpected {type(exc).__name__}", provider=entry.provider)
                wrapped.__cause__ = exc
                wrapped.gateway_attempts = getattr(exc, "gateway_attempts", 1)  # type: ignore[attr-defined]
                records.append(self._failure(entry, model_id, wrapped, "failed"))
                continue
            records.append(
                AttemptRecord(
                    provider=entry.provider, model=model_id, outcome="ok", attempts=attempts
                )
            )
            return _ChainOutcome(entry, model_id, value, records)

        raise _ChainError(AllProvidersFailed(alias, records), records)

    @staticmethod
    def _failure(
        entry: ModelEntry, model_id: str, exc: GatewayError, outcome: str
    ) -> AttemptRecord:
        return AttemptRecord(
            provider=entry.provider,
            model=model_id,
            outcome="skipped" if outcome == "skipped" else "failed",
            error_type=type(exc).__name__,
            reason=exc.describe(),
            attempts=int(getattr(exc, "gateway_attempts", 1)),
        )

    def _finish(
        self,
        operation: str,
        request_id: str,
        alias: str,
        start: float,
        records: list[AttemptRecord],
        metadata: dict[str, Any] | None,
        *,
        error: GatewayError | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost: float | None = None,
        fallback_used: bool = False,
        content: tuple[str | None, list[Message] | list[str], str | None] | None = None,
    ) -> float:
        """Emit the single structured log line and run hooks. Returns latency in ms."""
        latency = round((time.perf_counter() - start) * 1000, 2)
        status: Literal["ok", "error"] = "ok" if error is None else "error"
        who = _context_fields(metadata)
        record = hooks.CallRecord(
            request_id=request_id,
            operation="embed" if operation == "embed" else "complete",
            alias=alias,
            provider=provider,
            model=model_id,
            status=status,
            error_type=type(error).__name__ if error else None,
            attempts=sum(r.attempts for r in records),
            fallback_used=fallback_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency,
            metadata=dict(metadata or {}),
            attempt_records=list(records),
            tenant_id=who[0],
            user_id=who[1],
            product=who[2],
        )
        if content is not None and hooks.capture_content():
            record.content = _call_content(*content)
        fields: dict[str, Any] = {
            "request_id": request_id,
            "operation": record.operation,
            "alias": alias,
            "provider": provider,
            "model": model_id,
            "attempts": record.attempts,
            "fallback_used": fallback_used,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "cost_unknown": status == "ok" and cost is None,
            "latency_ms": latency,
            "status": status,
            "error_type": record.error_type,
            "chain": [
                {"provider": r.provider, "outcome": r.outcome, "reason": r.reason} for r in records
            ],
        }
        if content is not None and debug_log_prompts(self.environ):
            _system, msgs, output = content
            fields["prompt"] = [m.text() if isinstance(m, Message) else m for m in msgs]
            fields["response"] = output
        level = log.info if error is None else log.error
        level("gateway.call", extra={"fields": fields})
        for hook in list(hooks.on_call_complete):
            try:
                hook(record)
            except Exception:  # never let a hook break a call
                log.exception("gateway.hook_failed", extra={"fields": {"request_id": request_id}})
        return latency


def _context_fields(
    metadata: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    """(tenant_id, user_id, product): tenant/user from the request context, product
    from ``metadata["product"]`` or the context."""
    ctx = current()
    product = (metadata or {}).get("product") or (ctx.product if ctx else None)
    return (
        str(ctx.tenant_id) if ctx and ctx.tenant_id else None,
        str(ctx.user_id) if ctx and ctx.user_id else None,
        str(product) if product else None,
    )


def _call_content(
    system: str | None, items: list[Message] | list[str], output: str | None
) -> hooks.CallContent:
    messages: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, Message):
            text = "".join(p.text if isinstance(p, TextPart) else "[image]" for p in item.parts())
            messages.append({"role": item.role, "content": text})
        else:
            messages.append({"role": "input", "content": item})
    return hooks.CallContent(system=system, messages=messages, output=output)


_default: Gateway | None = None


def get_gateway() -> Gateway:
    """The process-wide default gateway (created on first use)."""
    global _default
    if _default is None:
        _default = Gateway()
    return _default


def set_gateway(gateway: Gateway | None) -> None:
    """Replace the default gateway (tests, custom config). ``None`` resets it."""
    global _default
    _default = gateway
