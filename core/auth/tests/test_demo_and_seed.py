"""The demo app and the seed script (both loaded from their file paths)."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ai_platform_auth import ConfigError
from ai_platform_auth.models import AuditLog, Role, Tenant, User
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .factories import TEST_SECRET

ROOT = Path(__file__).resolve().parents[3]
PASSWORDS = {
    "DEMO_ADMIN_PASSWORD": "demo-admin-passphrase",
    "DEMO_ANALYST_PASSWORD": "demo-analyst-passphrase",
    "DEMO_VIEWER_PASSWORD": "demo-viewer-passphrase",
    "DEMO_REVIEWER_PASSWORD": "demo-reviewer-passphrase",
}


def load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _restore_gateway_hooks() -> Iterator[None]:
    """The demo app registers the observability recorder process-wide; undo it."""
    from ai_platform_gateway import hooks, set_guard
    from ai_platform_observability.recorder import unregister_recorders
    from ai_platform_observability.redaction import set_content_redactor

    saved = list(hooks.on_call_complete)
    yield
    unregister_recorders()
    hooks.on_call_complete[:] = saved
    set_guard(None)  # the demo app installs the guardrails guard process-wide
    set_content_redactor(None)


@pytest.fixture(scope="module")
def seed_mod() -> ModuleType:
    return load(ROOT / "scripts" / "seed_demo.py", "seed_demo")


@pytest.fixture(scope="module")
def demo_mod() -> ModuleType:
    return load(ROOT / "core" / "auth" / "demo_app.py", "demo_app")


def test_seed_requires_password_env_vars(seed_mod: Any) -> None:
    with pytest.raises(seed_mod.SeedError, match="DEMO_ADMIN_PASSWORD"):
        seed_mod.read_passwords({})
    assert seed_mod.read_passwords(PASSWORDS)["demo-viewer"] == "demo-viewer-passphrase"


def test_seed_creates_demo_tenant_and_is_idempotent(seed_mod: Any, session: Session) -> None:
    pw = seed_mod.read_passwords(PASSWORDS)
    first = seed_mod.seed(session, pw)
    assert first[0] == "created tenant demo" and len(first) == 5
    tenant = session.scalar(select(Tenant).where(Tenant.slug == "demo"))
    assert tenant is not None
    users = {
        u.username: u.role for u in session.scalars(select(User).where(User.tenant_id == tenant.id))
    }
    assert users == {"demo-admin": Role.ADMIN, "demo-analyst": Role.ANALYST,
                     "demo-viewer": Role.VIEWER, "demo-reviewer": Role.ANALYST}  # fmt: skip
    approvers = [u for u, r in users.items() if r in (Role.ANALYST, Role.ADMIN)]
    assert len(approvers) >= 3  # any requester still leaves two people who can decide
    again = seed_mod.seed(session, pw)
    assert again == ["kept existing demo-admin", "kept existing demo-analyst",
                     "kept existing demo-viewer", "kept existing demo-reviewer"]  # fmt: skip
    reset = seed_mod.seed(session, pw, reset_passwords=True)
    assert reset[0] == "reset password for demo-admin"
    dump = str([r.details for r in session.scalars(select(AuditLog))])
    assert all(p not in dump for p in PASSWORDS.values())


def test_seed_main_fails_cleanly_without_config(
    seed_mod: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for var in PASSWORDS:
        monkeypatch.delenv(var, raising=False)
    assert seed_mod.main([]) == 2
    assert "missing env vars" in capsys.readouterr().err


def test_demo_app_refuses_to_start_without_jwt_secret(
    demo_mod: Any, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ConfigError):
        demo_mod.create_app(session_factory)
    monkeypatch.setenv("JWT_SECRET", "x" * 31)
    with pytest.raises(ConfigError):
        demo_mod.create_app(session_factory)


def test_demo_app_role_endpoints(
    demo_mod: Any, seed_mod: Any, session: Session, session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET.decode())
    seed_mod.seed(session, seed_mod.read_passwords(PASSWORDS))
    session.commit()
    app = demo_mod.create_app(session_factory)
    expected = {
        "demo-viewer": (200, 403, 403),
        "demo-analyst": (200, 200, 403),
        "demo-admin": (200, 200, 200),
    }
    with TestClient(app) as client:
        for username, codes in expected.items():
            pw = PASSWORDS[f"DEMO_{username.split('-')[1].upper()}_PASSWORD"]
            r = client.post("/auth/login", data={"username": f"demo/{username}", "password": pw})
            assert r.status_code == 200, r.text
            h = {"Authorization": f"Bearer {r.json()['access_token']}"}
            got = tuple(client.get(f"/demo/{area}", headers=h).status_code
                        for area in ("viewer", "analyst", "admin"))  # fmt: skip
            assert got == codes, username
            note = client.post("/demo/notes", headers={**h, "X-Request-ID": f"note-{username}"})
            assert note.status_code == (403 if username == "demo-viewer" else 201)
        # risky action -> approval request; the requester can't approve it, a reviewer can
        h_viewer = login_header(client, "demo-viewer")
        r = client.post("/demo/refunds?member=Ana%20Ruiz&amount=42", headers=h_viewer)
        assert r.status_code == 202 and r.json()["status"] == "pending"
        aid = r.json()["approval_id"]
        h_reviewer = login_header(client, "demo-reviewer")
        r = client.post(f"/approvals/{aid}/approve", headers=h_reviewer, json={"reason": "ok"})
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert "Ana Ruiz" not in r.json()["payload_summary"]
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").status_code == 200
        assert b"http_requests_total" in client.get("/metrics").content
    row = session.scalar(select(AuditLog).where(AuditLog.request_id == "note-demo-analyst"))
    assert row is not None and row.action == "http.post" and row.details == {"status": 201}


def login_header(client: TestClient, username: str) -> dict[str, str]:
    pw = PASSWORDS[f"DEMO_{username.split('-')[1].upper()}_PASSWORD"]
    r = client.post("/auth/login", data={"username": f"demo/{username}", "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ------------------------------------------------------------------ deployment behaviour (ADR 0008)


def test_demo_is_not_indexed(
    demo_mod: Any, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET.decode())
    with TestClient(demo_mod.create_app(session_factory)) as client:
        r = client.get("/robots.txt")
        assert r.status_code == 200 and r.text == "User-agent: *\nDisallow: /\n"
        assert client.get("/healthz").headers["X-Robots-Tag"] == "noindex, nofollow"
        assert client.get("/nope").headers["X-Robots-Tag"] == "noindex, nofollow"


def test_no_migrations_at_startup_unless_asked(
    demo_mod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    for name in ("migrate", "migrate_observability", "migrate_guardrails"):
        monkeypatch.setattr(demo_mod, name, lambda _url, n=name: calls.append(n))
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET.decode())
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/none")
    monkeypatch.delenv("RUN_MIGRATIONS_ON_START", raising=False)
    demo_mod.create_app()
    assert calls == []
    monkeypatch.setenv("RUN_MIGRATIONS_ON_START", "true")
    demo_mod.create_app()
    assert calls == ["migrate", "migrate_observability", "migrate_guardrails"]


def test_models_load_lazily_not_at_startup_or_login(
    demo_mod: Any, seed_mod: Any, session: Session, session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Health checks, crawlers and logins must not pay the model-load cost."""
    from ai_platform_guardrails.injection import ml
    from ai_platform_guardrails.pii.engine import get_analyzer

    monkeypatch.setenv("JWT_SECRET", TEST_SECRET.decode())
    seed_mod.seed(session, seed_mod.read_passwords(PASSWORDS))
    session.commit()
    get_analyzer.cache_clear()
    ml._default.cache_clear()
    with TestClient(demo_mod.create_app(session_factory)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/robots.txt").status_code == 200
        login_header(client, "demo-viewer")
    assert get_analyzer.cache_info().currsize == 0
    assert ml._default.cache_info().currsize == 0


def test_diagnostics_route_only_when_enabled(
    demo_mod: Any, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET.decode())
    monkeypatch.delenv("DEMO_DIAGNOSTICS", raising=False)
    with TestClient(demo_mod.create_app(session_factory)) as client:
        assert client.get("/diag/warmup").status_code == 404
    monkeypatch.setenv("DEMO_DIAGNOSTICS", "true")
    monkeypatch.setenv("GUARD_ML_CLASSIFIER", "false")  # the auth suite doesn't need the model
    with TestClient(demo_mod.create_app(session_factory)) as client:
        body = client.get("/diag/warmup").json()
    assert set(body) == {"spacy_ms", "classifier_ms", "classifier_score", "peak_rss_mb"}
    assert body["classifier_score"] == -1.0 and body["peak_rss_mb"] > 0
    with TestClient(demo_mod.create_app(session_factory)) as client:
        info = client.get("/diag/client", headers={"X-Forwarded-For": "203.0.113.9"}).json()
    assert info["x_forwarded_for"] == "203.0.113.9" and info["peer"] == "testclient"
    monkeypatch.delenv("DEMO_DIAGNOSTICS")
    with TestClient(demo_mod.create_app(session_factory)) as client:
        assert client.get("/diag/client").status_code == 404
