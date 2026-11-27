"""The post-deploy smoke check, against a scripted HTTP transport."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import httpx
import pytest

PATH = Path(__file__).resolve().parents[3] / "scripts" / "smoke_check.py"


@pytest.fixture(scope="module")
def script() -> Any:  # noqa: ANN401 - a module loaded from a file path
    spec = importlib.util.spec_from_file_location("smoke_check", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client(ready: list[int | Exception], login: int = 200, ask: tuple[int, str] = (200, "")
            ) -> httpx.Client:  # fmt: skip
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/readyz":
            step = ready.pop(0) if len(ready) > 1 else ready[0]
            if isinstance(step, Exception):
                raise step
            return httpx.Response(step)
        if request.url.path == "/auth/login":
            assert b"demo%2Fdemo-viewer" in request.content
            return httpx.Response(login, json={"access_token": "tok"} if login == 200 else {})
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(ask[0], json={"answer": "ok", "detail": ask[1]})

    return httpx.Client(base_url="https://demo.invalid", transport=httpx.MockTransport(handler))


def _run(script: Any, client: httpx.Client, timeout: float = 600) -> list[str]:  # noqa: ANN401
    t = [0.0]

    def sleep(s: float) -> None:
        t[0] += s

    return list(script.smoke(client, "pw", ready_timeout_s=timeout, sleep=sleep,
                             clock=lambda: t[0]))  # fmt: skip


def test_healthy_after_waking_up(script: Any) -> None:  # noqa: ANN401
    waking: list[int | Exception] = [httpx.ConnectError("asleep"), 503, 200]
    assert _run(script, _client(waking)) == []


def test_demo_limit_reached_counts_as_healthy(script: Any) -> None:  # noqa: ANN401
    assert _run(script, _client([200], ask=(429, "Demo limit reached. ..."))) == []


@pytest.mark.parametrize(
    ("client_kwargs", "expected"),
    [
        ({"ready": [503]}, "/readyz never returned 200 (last: HTTP 503)"),
        ({"ready": [httpx.ConnectError("x")]}, "/readyz never returned 200 (last: ConnectError)"),
        ({"ready": [200], "login": 401}, "guest login failed: HTTP 401"),
        ({"ready": [200], "ask": (503, "busy")}, "/demo/ask failed: HTTP 503: busy"),
        ({"ready": [200], "ask": (429, "Too many requests")}, "/demo/ask failed: HTTP 429"),
    ],
)
def test_failures(script: Any, client_kwargs: dict[str, Any], expected: str) -> None:  # noqa: ANN401
    problems = _run(script, _client(**client_kwargs), timeout=30)
    assert len(problems) == 1 and problems[0].startswith(expected)


def test_main_needs_config(
    script: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],  # noqa: ANN401
) -> None:
    monkeypatch.delenv("DEMO_BASE_URL", raising=False)
    assert script.main([]) == 2
    assert "DEMO_BASE_URL" in capsys.readouterr().err


def test_main_reports(
    script: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],  # noqa: ANN401
) -> None:
    monkeypatch.setenv("DEMO_BASE_URL", "https://demo.invalid")
    monkeypatch.setenv("DEMO_VIEWER_PASSWORD", "secret-pw")
    monkeypatch.setattr(script, "smoke", lambda _c, _p: [])
    assert script.main([]) == 0
    monkeypatch.setattr(script, "smoke", lambda _c, _p: ["boom"])
    assert script.main([]) == 1
    out = capsys.readouterr()
    assert "FAIL boom" in out.err and "secret-pw" not in out.out + out.err
