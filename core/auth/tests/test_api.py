"""End-to-end API tests through FastAPI's TestClient (real Postgres, no network).

No ``from __future__ import annotations`` here: the test app's endpoints reference the
``auth`` fixture value inside ``Depends(...)``, which FastAPI must evaluate eagerly.
"""

import json
import logging
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from ai_platform_auth import Auth, AuthSettings, ConfigError, Role, TenantScope, User
from ai_platform_auth.models import AuditLog
from ai_platform_auth.ratelimit import InMemoryRateLimiter
from ai_platform_common.logging import JsonFormatter
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .factories import PASSWORD, TEST_SECRET, FakeClock, make_tenant, make_user

SETTINGS = AuthSettings(jwt_secret=TEST_SECRET, login_rate_limit=50)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def auth(session_factory: sessionmaker[Session], clock: FakeClock) -> Auth:
    return Auth(SETTINGS, session_factory, clock=clock)


@pytest.fixture
def app(auth: Auth) -> FastAPI:
    app = FastAPI()
    auth.install(app)

    @app.get("/viewer")
    def viewer(user: Annotated[User, Depends(auth.require_role("viewer"))]) -> dict[str, str]:
        return {"user": user.username}

    @app.get("/analyst", dependencies=[Depends(auth.require_role(Role.ANALYST))])
    def analyst() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/admin", dependencies=[Depends(auth.require_role("admin"))])
    def admin() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/scope")
    def scope(s: Annotated[TenantScope, Depends(auth.get_tenant_scope)]) -> dict[str, str]:
        return {"tenant_id": str(s.tenant_id)}

    @app.post("/notes", dependencies=[Depends(auth.require_role("analyst"))])
    def notes() -> dict[str, bool]:
        return {"saved": True}

    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tenant_users(session: Session) -> dict[str, User]:
    t = make_tenant(session, "acme")
    users = {
        "admin": make_user(session, t, "ada", Role.ADMIN),
        "analyst": make_user(session, t, "ana", Role.ANALYST),
        "viewer": make_user(session, t, "vic", Role.VIEWER),
    }
    session.commit()
    return users


def login(client: TestClient, username: str, tenant: str = "acme", password: str = PASSWORD,
          **headers: str) -> dict[str, Any]:  # fmt: skip
    r = client.post(
        "/auth/login", data={"username": username, "password": password, "tenant": tenant},
        headers=headers,
    )  # fmt: skip
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def audit_rows(session: Session) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


# ------------------------------------------------------------------ login / me / refresh / logout
def test_login_me_refresh_logout_flow(
    client: TestClient, session: Session, tenant_users: dict[str, User]
) -> None:
    tokens = login(client, "ana", **{"X-Request-ID": "req-flow-1"})
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 900
    me = client.get("/auth/me", headers=bearer(tokens))
    assert me.status_code == 200
    assert me.json()["username"] == "ana" and me.json()["role"] == "analyst"
    assert "password_hash" not in me.json()

    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != tokens["refresh_token"]

    r = client.post("/auth/logout", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 204
    r = client.post("/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 401

    rows = audit_rows(session)
    acts = [a.action for a in rows]
    assert {"auth.login", "auth.refresh", "auth.logout"} <= set(acts)
    login_row = next(a for a in rows if a.action == "auth.login")
    assert login_row.request_id == "req-flow-1"
    assert login_row.ip == "testclient"
    # explicit audits are not duplicated by the generic write audit
    assert not any(a.startswith("http.post") and r.resource_id in ("/auth/login",
                   "/auth/refresh") for a, r in zip(acts, rows, strict=True))  # fmt: skip


def test_tenant_in_username_and_generic_errors(
    client: TestClient, tenant_users: dict[str, User]
) -> None:
    r = client.post("/auth/login", data={"username": "acme/vic", "password": PASSWORD})
    assert r.status_code == 200
    bodies = []
    for data in (
        {"username": "vic", "password": "wrong-password-xyz", "tenant": "acme"},
        {"username": "ghost", "password": PASSWORD, "tenant": "acme"},
        {"username": "vic", "password": PASSWORD, "tenant": "nope"},
        {"username": "vic", "password": PASSWORD},
    ):
        r = client.post("/auth/login", data=data)
        assert r.status_code == 401
        assert r.headers["WWW-Authenticate"] == "Bearer"
        bodies.append(r.json())
    assert all(b == {"detail": "Invalid credentials"} for b in bodies)


def test_locked_account_gets_generic_401(
    client: TestClient, session: Session, tenant_users: dict[str, User]
) -> None:
    for _ in range(5):
        client.post("/auth/login", data={"username": "vic", "password": "bad-password-123",
                                         "tenant": "acme"})  # fmt: skip
    r = client.post("/auth/login", data={"username": "vic", "password": PASSWORD,
                                         "tenant": "acme"})  # fmt: skip
    assert r.status_code == 401 and r.json() == {"detail": "Invalid credentials"}
    assert "auth.lockout" in [a.action for a in audit_rows(session)]


def test_request_id_generated_and_unsafe_ids_replaced(
    client: TestClient, tenant_users: dict[str, User]
) -> None:
    r = client.get("/auth/me")
    assert len(r.headers["X-Request-ID"]) == 36
    r = client.get("/auth/me", headers={"X-Request-ID": "bad id\nwith newline"})
    assert r.headers["X-Request-ID"] != "bad id\nwith newline"
    r = client.get("/auth/me", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


# ------------------------------------------------------------------ roles
@pytest.mark.parametrize(
    ("who", "viewer", "analyst", "admin"),
    [("vic", 200, 403, 403), ("ana", 200, 200, 403), ("ada", 200, 200, 200)],
)
def test_role_hierarchy_on_endpoints(
    client: TestClient, tenant_users: dict[str, User], who: str, viewer: int, analyst: int,
    admin: int,
) -> None:  # fmt: skip
    h = bearer(login(client, who))
    assert client.get("/viewer", headers=h).status_code == viewer
    assert client.get("/analyst", headers=h).status_code == analyst
    assert client.get("/admin", headers=h).status_code == admin
    assert client.get("/auth/users", headers=h).status_code == admin


def test_unauthenticated_and_bad_tokens(client: TestClient, tenant_users: dict[str, User]) -> None:
    assert client.get("/viewer").status_code == 401
    assert client.get("/viewer", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_deactivated_user_rejected_immediately(
    client: TestClient, tenant_users: dict[str, User]
) -> None:
    admin_h = bearer(login(client, "ada"))
    vic_h = bearer(login(client, "vic"))
    vic_id = str(tenant_users["viewer"].id)
    r = client.patch(f"/auth/users/{vic_id}", json={"is_active": False}, headers=admin_h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/viewer", headers=vic_h).status_code == 401  # same access token


# ------------------------------------------------------------------ admin user management
def test_admin_creates_lists_and_updates_users(
    client: TestClient, session: Session, tenant_users: dict[str, User]
) -> None:
    h = bearer(login(client, "ada"))
    r = client.post("/auth/users", headers=h, json={
        "username": "newbie", "email": "newbie@example.com",
        "password": "a-very-long-password", "role": "viewer"})  # fmt: skip
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert r.json()["tenant_id"] == str(tenant_users["admin"].tenant_id)
    dup = client.post("/auth/users", headers=h, json={
        "username": "newbie", "email": "x@example.com",
        "password": "a-very-long-password"})  # fmt: skip
    assert dup.status_code == 409
    weak = client.post("/auth/users", headers=h, json={
        "username": "weak", "email": "w@example.com", "password": "short"})  # fmt: skip
    assert weak.status_code == 422
    names = [u["username"] for u in client.get("/auth/users", headers=h).json()]
    assert names == ["ada", "ana", "newbie", "vic"]
    r = client.patch(f"/auth/users/{new_id}", headers=h, json={"role": "analyst", "unlock": True})
    assert r.status_code == 200 and r.json()["role"] == "analyst"
    self_demote = client.patch(
        f"/auth/users/{tenant_users['admin'].id}", headers=h, json={"role": "viewer"}
    )
    assert self_demote.status_code == 403
    acts = [a.action for a in audit_rows(session)]
    assert acts.count("user.create") == 1 and acts.count("user.update") == 1


def test_cross_tenant_access_is_blocked(
    client: TestClient, session: Session, tenant_users: dict[str, User]
) -> None:
    other = make_tenant(session, "globex")
    outsider = make_user(session, other, "otto", Role.ADMIN)
    session.commit()
    h = bearer(login(client, "ada"))
    # tenant A admin cannot see, read or modify tenant B's user
    assert "otto" not in [u["username"] for u in client.get("/auth/users", headers=h).json()]
    r = client.patch(f"/auth/users/{outsider.id}", headers=h, json={"is_active": False})
    assert r.status_code == 404
    # tenant_id in the body is ignored: users are created in the caller's tenant
    r = client.post("/auth/users", headers=h, json={
        "username": "sneaky", "email": "s@example.com", "password": "a-very-long-password",
        "tenant_id": str(other.id)})  # fmt: skip
    assert r.status_code == 201 and r.json()["tenant_id"] == str(tenant_users["admin"].tenant_id)
    # tenant scope comes from the token
    other_h = bearer(login(client, "otto", tenant="globex"))
    assert client.get("/scope", headers=other_h).json() == {"tenant_id": str(other.id)}
    assert client.get("/scope", headers=h).json()["tenant_id"] != str(other.id)


# ------------------------------------------------------------------ tenants
def test_superadmin_manages_tenants(
    client: TestClient, session: Session, tenant_users: dict[str, User]
) -> None:
    home = make_tenant(session, "platform")
    make_user(session, home, "root", Role.ADMIN, superadmin=True)
    session.commit()
    admin_h = bearer(login(client, "ada"))
    assert client.post("/auth/tenants", headers=admin_h,
                       json={"slug": "x", "name": "X"}).status_code == 403  # fmt: skip
    root_h = bearer(login(client, "root", tenant="platform"))
    r = client.post("/auth/tenants", headers=root_h, json={"slug": "initech", "name": "Initech"})
    assert r.status_code == 201
    slugs = [t["slug"] for t in client.get("/auth/tenants", headers=root_h).json()]
    assert {"acme", "platform", "initech"} <= set(slugs)


# ------------------------------------------------------------------ audit middleware
def test_generic_write_endpoints_are_audited(
    client: TestClient, session: Session, tenant_users: dict[str, User]
) -> None:
    h = bearer(login(client, "ana"))
    r = client.post("/notes", headers={**h, "X-Request-ID": "req-notes"})
    assert r.status_code == 200
    row = session.scalar(select(AuditLog).where(AuditLog.request_id == "req-notes"))
    assert row is not None
    assert row.action == "http.post" and row.resource_id == "/notes"
    assert row.user_id == tenant_users["analyst"].id and row.details == {"status": 200}
    # a forbidden write is audited too, with its status
    vh = bearer(login(client, "vic"))
    client.post("/notes", headers={**vh, "X-Request-ID": "req-denied"})
    denied = session.scalar(select(AuditLog).where(AuditLog.request_id == "req-denied"))
    assert denied is not None and denied.details == {"status": 403}
    # reads are not audited
    client.get("/viewer", headers={**h, "X-Request-ID": "req-read"})
    assert session.scalar(select(AuditLog).where(AuditLog.request_id == "req-read")) is None


# ------------------------------------------------------------------ rate limit
def test_login_rate_limited_per_ip(
    session_factory: sessionmaker[Session], session: Session, tenant_users: dict[str, User]
) -> None:
    auth = Auth(SETTINGS, session_factory, rate_limiter=InMemoryRateLimiter(3, 60))
    app = FastAPI()
    auth.install(app)
    with TestClient(app) as c:
        for _ in range(3):
            c.post("/auth/login", data={"username": "x", "password": "y", "tenant": "acme"})
        r = c.post("/auth/login", data={"username": "vic", "password": PASSWORD, "tenant": "acme"})
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) > 0
    assert "auth.login_rate_limited" in [a.action for a in audit_rows(session)]


def test_forwarded_for_only_from_the_trusted_proxy(
    session_factory: sessionmaker[Session], session: Session, tenant_users: dict[str, User]
) -> None:
    """A visitor can't fake their IP: X-Forwarded-For counts only via the trusted proxy,
    and only the hop the proxy appended (the right-most untrusted address)."""
    cases = [
        # (trust on?, TCP peer, header, ip recorded in the audit log)
        (False, "10.0.0.2", "203.0.113.7", "10.0.0.2"),
        (True, "198.51.100.4", "203.0.113.7", "198.51.100.4"),  # not via the proxy
        (True, "10.0.0.2", "6.6.6.6, 203.0.113.7", "203.0.113.7"),  # spoof on the left
    ]
    for i, (trusted, peer, xff, expected) in enumerate(cases):
        settings = AuthSettings(jwt_secret=TEST_SECRET, trust_proxy_headers=trusted,
                                trusted_proxy_cidrs=("10.0.0.0/8",))  # fmt: skip
        app = FastAPI()
        Auth(settings, session_factory).install(app)
        rid = f"req-xff-{i}"
        with TestClient(app, client=(peer, 50000)) as c:
            c.post("/auth/login", headers={"X-Forwarded-For": xff, "X-Request-ID": rid},
                   data={"username": "vic", "password": PASSWORD, "tenant": "acme"})  # fmt: skip
        row = session.scalar(select(AuditLog).where(AuditLog.request_id == rid))
        assert row is not None and row.ip == expected, (trusted, peer, xff)


def test_spoofed_forwarded_for_cannot_dodge_the_login_rate_limit(
    session_factory: sessionmaker[Session], tenant_users: dict[str, User]
) -> None:
    settings = AuthSettings(jwt_secret=TEST_SECRET, login_rate_limit=3, trust_proxy_headers=True,
                            trusted_proxy_cidrs=("10.0.0.0/8",))  # fmt: skip
    app = FastAPI()
    Auth(settings, session_factory).install(app)
    with TestClient(app, client=("10.0.0.2", 50000)) as c:
        codes = [
            c.post("/auth/login", headers={"X-Forwarded-For": f"9.9.9.{n}, 203.0.113.7"},
                   data={"username": "x", "password": "y", "tenant": "acme"}).status_code
            for n in range(5)
        ]  # fmt: skip
    assert codes[-1] == 429  # a new fake left-hand IP each time doesn't reset the limit


# ------------------------------------------------------------------ secrets and config
def test_secrets_never_logged(
    client: TestClient, tenant_users: dict[str, User], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        tokens = login(client, "ana")
        client.post("/auth/login", data={"username": "ana", "password": "wrong-password-42",
                                         "tenant": "acme"})  # fmt: skip
        r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})  # reuse
        client.get("/auth/me", headers=bearer(r.json()))
    fmt = JsonFormatter()
    text = "\n".join(fmt.format(rec) for rec in caplog.records)
    assert "audit" in text
    for secret in (PASSWORD, "wrong-password-42", tokens["access_token"], tokens["refresh_token"],
                   r.json()["refresh_token"], TEST_SECRET.decode()):  # fmt: skip
        assert secret not in text


def test_secrets_not_in_audit_log(
    client: TestClient, session: Session, tenant_users: dict[str, User]
) -> None:
    tokens = login(client, "ana")
    client.post("/auth/login", data={"username": "ana", "password": "wrong-password-42",
                                     "tenant": "acme"})  # fmt: skip
    dump = json.dumps([[r.action, r.details] for r in audit_rows(session)])
    assert PASSWORD not in dump and "wrong-password-42" not in dump
    assert tokens["refresh_token"] not in dump


def test_auth_from_env_refuses_bad_secret(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ConfigError):
        Auth.from_env(session_factory)
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(ConfigError):
        Auth.from_env(session_factory)
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET.decode())
    assert isinstance(Auth.from_env(session_factory), Auth)


def test_audit_write_failure_never_breaks_the_response(
    session_factory: sessionmaker[Session], tenant_users: dict[str, User],
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    from ai_platform_auth.middleware import AuditMiddleware

    def broken_factory() -> Session:
        raise RuntimeError("database down")

    app = FastAPI()

    @app.post("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(AuditMiddleware, session_factory=broken_factory)  # type: ignore[arg-type]
    with TestClient(app) as c, caplog.at_level(logging.ERROR):
        r = c.post("/ping")
    assert r.status_code == 200
    assert any(rec.getMessage() == "audit.write_failed" for rec in caplog.records)
