"""The demo app and the seed script (both loaded from their file paths)."""

import importlib.util
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
}


def load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    assert first[0] == "created tenant demo" and len(first) == 4
    tenant = session.scalar(select(Tenant).where(Tenant.slug == "demo"))
    assert tenant is not None
    users = {
        u.username: u.role for u in session.scalars(select(User).where(User.tenant_id == tenant.id))
    }
    assert users == {"demo-admin": Role.ADMIN, "demo-analyst": Role.ANALYST,
                     "demo-viewer": Role.VIEWER}  # fmt: skip
    again = seed_mod.seed(session, pw)
    assert again == ["kept existing demo-admin", "kept existing demo-analyst",
                     "kept existing demo-viewer"]  # fmt: skip
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
    row = session.scalar(select(AuditLog).where(AuditLog.request_id == "note-demo-analyst"))
    assert row is not None and row.action == "http.post" and row.details == {"status": 201}
