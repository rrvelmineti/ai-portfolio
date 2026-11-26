from __future__ import annotations

import pytest
from ai_platform_auth.errors import RateLimitExceeded
from ai_platform_auth.ratelimit import InMemoryRateLimiter, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_limit_window_and_retry_after() -> None:
    clock = FakeClock()
    rl: RateLimiter = InMemoryRateLimiter(limit=3, window_s=60, clock=clock)
    for _ in range(3):
        rl.hit("1.2.3.4")
    with pytest.raises(RateLimitExceeded) as info:
        rl.hit("1.2.3.4")
    assert info.value.retry_after == 60
    rl.hit("5.6.7.8")  # other keys unaffected
    clock.t += 30
    with pytest.raises(RateLimitExceeded) as info:
        rl.hit("1.2.3.4")
    assert info.value.retry_after == 30
    clock.t += 31  # window slid past the first hits
    rl.hit("1.2.3.4")


def test_reset() -> None:
    rl = InMemoryRateLimiter(limit=1, window_s=60, clock=FakeClock())
    rl.hit("k")
    rl.reset("k")
    rl.hit("k")


def test_prunes_stale_keys() -> None:
    clock = FakeClock()
    rl = InMemoryRateLimiter(limit=5, window_s=10, clock=clock)
    for i in range(10_000):
        rl.hit(f"ip-{i}")
    clock.t += 11
    rl.hit("fresh")
    assert len(rl._hits) == 1
