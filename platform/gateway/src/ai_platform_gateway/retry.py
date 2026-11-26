"""Retry policy: exponential backoff with jitter, only on retryable errors."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ai_platform_gateway.errors import RETRYABLE_ERRORS

T = TypeVar("T")


@dataclass
class RetryPolicy:
    """How one provider call is retried before failing over.

    Only ``RateLimited`` and ``ProviderUnavailable`` (incl. timeouts) are retried.
    """

    max_attempts: int = 3
    initial_wait_s: float = 0.5
    max_wait_s: float = 8.0
    jitter_s: float = 0.5
    sleep: Callable[[float], None] = field(default=time.sleep)

    def call(self, fn: Callable[[], T]) -> tuple[T, int]:
        """Run ``fn`` with retries. Returns ``(result, attempts)``.

        On final failure the last exception is re-raised with ``gateway_attempts`` set.
        """
        count = 0

        def counted() -> T:
            nonlocal count
            count += 1
            return fn()

        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential_jitter(
                initial=self.initial_wait_s, max=self.max_wait_s, jitter=self.jitter_s
            ),
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            sleep=self.sleep,
            reraise=True,
        )
        try:
            return retrying(counted), count
        except Exception as exc:
            exc.gateway_attempts = count  # type: ignore[attr-defined]
            raise
