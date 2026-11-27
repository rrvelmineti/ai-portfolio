"""Smoke-check a deployed demo (run by the deploy workflow after each deploy; ADR 0008).

    DEMO_BASE_URL=https://... DEMO_VIEWER_PASSWORD=... uv run python scripts/smoke_check.py

Checks, in order:
1. ``GET /readyz`` returns 200 (retried while the host wakes up or rolls out);
2. the guest ``demo/demo-viewer`` can log in;
3. one ``POST /demo/ask`` succeeds with guardrails on (a "demo limit reached" 429 also
   counts as healthy: the app answered, the caps did their job).

Exit 0 when healthy, 1 when a check fails (the workflow then rolls back), 2 on missing
configuration. Prints no tokens or passwords.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

import httpx
from ai_platform_common.config import get_env

QUESTION = "In one sentence, what does a claims onboarding assistant do?"


def smoke(
    client: httpx.Client,
    password: str,
    *,
    ready_timeout_s: float = 600,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[str]:
    """Run the checks; returns problems (empty when healthy)."""
    deadline = clock() + ready_timeout_s
    while True:
        try:
            status = client.get("/readyz").status_code
        except httpx.HTTPError as exc:
            status, error = 0, type(exc).__name__
        else:
            error = f"HTTP {status}"
        if status == 200:
            break
        if clock() >= deadline:
            return [f"/readyz never returned 200 (last: {error})"]
        sleep(10)
    login = client.post("/auth/login", data={"username": "demo/demo-viewer", "password": password})
    if login.status_code != 200:
        return [f"guest login failed: HTTP {login.status_code}"]
    token = login.json()["access_token"]
    r = client.post("/demo/ask", json={"question": QUESTION},
                    headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    if r.status_code == 429 and str(r.json().get("detail", "")).startswith("Demo limit reached"):
        return []
    if r.status_code != 200:
        return [f"/demo/ask failed: HTTP {r.status_code}: {r.json().get('detail')}"]
    return []


def main(argv: list[str] | None = None) -> int:
    base_url, password = get_env("DEMO_BASE_URL"), get_env("DEMO_VIEWER_PASSWORD")
    if not base_url or not password:
        print("missing env vars: DEMO_BASE_URL and DEMO_VIEWER_PASSWORD", file=sys.stderr)
        return 2
    with httpx.Client(base_url=base_url, timeout=60, follow_redirects=True) as client:
        problems = smoke(client, password)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if not problems:
        print("smoke check passed: /readyz, guest login, one guarded AI call")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
