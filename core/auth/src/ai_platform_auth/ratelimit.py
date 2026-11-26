"""Login rate limiting behind a small interface, so Redis can replace memory later."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

from ai_platform_auth.errors import RateLimitExceeded


class RateLimiter(Protocol):
    """Counts attempts per key (e.g. client IP) in a time window."""

    def hit(self, key: str) -> None:
        """Record one attempt; raise ``RateLimitExceeded`` if over the limit."""
        ...

    def reset(self, key: str) -> None:
        """Forget a key's attempts."""
        ...


class InMemoryRateLimiter:
    """Sliding-window limiter for a single process. Thread-safe.

    Fine for one API instance; use a shared store (Redis) once there are several.
    """

    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(hits[0] + self.window_s - now))
                raise RateLimitExceeded(retry_after)
            hits.append(now)
            self._prune(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _prune(self, now: float) -> None:
        """Drop keys with no recent hits so memory stays bounded."""
        if len(self._hits) < 10_000:
            return
        stale = [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window_s]
        for k in stale:
            del self._hits[k]
