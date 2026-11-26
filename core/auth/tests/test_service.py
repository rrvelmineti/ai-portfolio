from __future__ import annotations

from datetime import timedelta

import pytest
from ai_platform_auth.audit import RequestInfo
from ai_platform_auth.errors import (
    Conflict,
    InvalidCredentials,
    NotFound,
    PermissionDenied,
    TokenInvalid,
    WeakPassword,
)
from ai_platform_auth.models import AuditLog, RefreshToken, Role
from ai_platform_auth.scope import TenantScope
from ai_platform_auth.service import AuthService, UserChanges, role_allows
from ai_platform_auth.settings import AuthSettings
from ai_platform_auth.tokens import decode_access_token, hash_refresh_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from .factories import PASSWORD, TEST_SECRET, FakeClock, make_tenant, make_user

SETTINGS = AuthSettings(jwt_secret=TEST_SECRET)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def svc(clock: FakeClock) -> AuthService:
    return AuthService(SETTINGS, clock=clock)


def req(rid: str = "req-1") -> RequestInfo:
    return RequestInfo(request_id=rid, ip="10.0.0.1", user_agent="pytest")


def actions(session: Session, **filters: object) -> list[str]:
    stmt = select(AuditLog.action).order_by(AuditLog.id)
    for k, v in filters.items():
        stmt = stmt.where(getattr(AuditLog, k) == v)
    return list(session.scalars(stmt))


def stored_token(session: Session, raw: str) -> RefreshToken | None:
    return session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
    )


# ------------------------------------------------------------------ login
def test_login_success(session: Session, svc: AuthService, clock: FakeClock) -> None:
    t = make_tenant(session, "acme")
    u = make_user(session, t, "alice", Role.ANALYST)
    pair = svc.login(session, "ACME", "alice", PASSWORD, req("r-login"))
    claims = decode_access_token(pair.access_token, SETTINGS)
    assert (claims.user_id, claims.tenant_id, claims.role) == (u.id, t.id, Role.ANALYST)
    assert pair.expires_in == 900 and pair.token_type == "bearer"
    session.refresh(u)
    assert u.last_login_at == clock.now
    row = session.scalar(select(AuditLog).where(AuditLog.action == "auth.login"))
    assert row is not None and row.request_id == "r-login" and row.user_id == u.id
    assert row.ip == "10.0.0.1"
    stored = session.scalar(select(RefreshToken).where(RefreshToken.user_id == u.id))
    assert stored is not None and stored.token_hash == hash_refresh_token(pair.refresh_token)
    assert stored.expires_at == clock.now + timedelta(days=7)


@pytest.mark.parametrize(
    ("tenant", "username", "password", "reason"),
    [
        ("acme", "alice", "wrong-password-123", "bad_password"),
        ("acme", "nobody", PASSWORD, "unknown_user"),
        ("nope", "alice", PASSWORD, "unknown_tenant"),
    ],
)
def test_login_failures_share_one_error(
    session: Session, svc: AuthService, tenant: str, username: str, password: str, reason: str
) -> None:
    t = make_tenant(session, "acme")
    make_user(session, t, "alice")
    with pytest.raises(InvalidCredentials) as info:
        svc.login(session, tenant, username, password, req())
    assert str(info.value) == "Invalid credentials"
    row = session.scalar(select(AuditLog).where(AuditLog.action == "auth.login_failed"))
    assert row is not None and row.details["reason"] == reason
    assert password not in str(row.details)


def test_lockout_after_five_failures_and_unlock_after_15_minutes(
    session: Session, svc: AuthService, clock: FakeClock
) -> None:
    t = make_tenant(session)
    u = make_user(session, t, "bob")
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            svc.login(session, t.slug, "bob", "wrong-password-123", req())
    session.refresh(u)
    assert u.locked_until == clock.now + timedelta(minutes=15)
    assert "auth.lockout" in actions(session, user_id=u.id)
    # correct password is rejected while locked, with the same error
    with pytest.raises(InvalidCredentials):
        svc.login(session, t.slug, "bob", PASSWORD, req())
    last = session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
    assert last is not None and last.details["reason"] == "locked"
    clock.advance(minutes=15, seconds=1)
    svc.login(session, t.slug, "bob", PASSWORD, req())
    session.refresh(u)
    assert u.locked_until is None and u.failed_logins == 0


def test_success_resets_failed_counter(session: Session, svc: AuthService) -> None:
    t = make_tenant(session)
    u = make_user(session, t, "carol")
    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            svc.login(session, t.slug, "carol", "wrong-password-123", req())
    svc.login(session, t.slug, "carol", PASSWORD, req())
    session.refresh(u)
    assert u.failed_logins == 0


def test_inactive_user_cannot_login(session: Session, svc: AuthService) -> None:
    t = make_tenant(session)
    u = make_user(session, t, "dan")
    u.is_active = False
    session.flush()
    with pytest.raises(InvalidCredentials):
        svc.login(session, t.slug, "dan", PASSWORD, req())


# ------------------------------------------------------------------ refresh
def test_refresh_rotates_token(session: Session, svc: AuthService, clock: FakeClock) -> None:
    t = make_tenant(session)
    u = make_user(session, t, "erin")
    first = svc.login(session, t.slug, "erin", PASSWORD, req())
    clock.advance(minutes=1)
    second = svc.refresh(session, first.refresh_token, req("r-refresh"))
    assert second.refresh_token != first.refresh_token
    old = session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(first.refresh_token)
        )
    )
    new = session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(second.refresh_token)
        )
    )
    assert old is not None and new is not None
    assert old.revoked_at == clock.now and old.replaced_by == new.id
    assert new.family_id == old.family_id and new.revoked_at is None
    assert "auth.refresh" in actions(session, user_id=u.id)
    # the new token works again
    svc.refresh(session, second.refresh_token, req())


def test_refresh_reuse_revokes_whole_chain(session: Session, svc: AuthService) -> None:
    t = make_tenant(session)
    u = make_user(session, t, "frank")
    first = svc.login(session, t.slug, "frank", PASSWORD, req())
    second = svc.refresh(session, first.refresh_token, req())
    # attacker replays the already-rotated first token
    with pytest.raises(TokenInvalid):
        svc.refresh(session, first.refresh_token, req("r-theft"))
    assert "auth.refresh_reuse_detected" in actions(session, user_id=u.id)
    # the legitimate latest token is now revoked too
    with pytest.raises(TokenInvalid):
        svc.refresh(session, second.refresh_token, req())
    active = session.scalars(
        select(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))
    ).all()
    assert active == []


def test_reuse_only_revokes_that_family(session: Session, svc: AuthService) -> None:
    t = make_tenant(session)
    make_user(session, t, "gina")
    laptop = svc.login(session, t.slug, "gina", PASSWORD, req())
    phone = svc.login(session, t.slug, "gina", PASSWORD, req())
    svc.refresh(session, laptop.refresh_token, req())
    with pytest.raises(TokenInvalid):
        svc.refresh(session, laptop.refresh_token, req())
    svc.refresh(session, phone.refresh_token, req())  # other session unaffected


def test_refresh_expired_unknown_and_inactive(
    session: Session, svc: AuthService, clock: FakeClock
) -> None:
    t = make_tenant(session)
    u = make_user(session, t, "hank")
    pair = svc.login(session, t.slug, "hank", PASSWORD, req())
    with pytest.raises(TokenInvalid):
        svc.refresh(session, "not-a-real-token", req())
    clock.advance(days=7, seconds=1)
    with pytest.raises(TokenInvalid, match="expired"):
        svc.refresh(session, pair.refresh_token, req())
    clock.advance(days=-8)
    pair2 = svc.login(session, t.slug, "hank", PASSWORD, req())
    u.is_active = False
    session.flush()
    with pytest.raises(TokenInvalid):
        svc.refresh(session, pair2.refresh_token, req())


def test_logout_revokes_family(session: Session, svc: AuthService) -> None:
    t = make_tenant(session)
    u = make_user(session, t, "ivy")
    pair = svc.login(session, t.slug, "ivy", PASSWORD, req())
    svc.logout(session, pair.refresh_token, req("r-logout"))
    with pytest.raises(TokenInvalid):
        svc.refresh(session, pair.refresh_token, req())
    assert "auth.logout" in actions(session, user_id=u.id)
    svc.logout(session, "unknown-token", req())  # idempotent, no error


# ------------------------------------------------------------------ users and isolation
def test_admin_user_management_and_audit(session: Session, svc: AuthService) -> None:
    t = make_tenant(session)
    admin = make_user(session, t, "root", Role.ADMIN)
    scope = TenantScope(t.id)
    new = svc.create_user(
        session, admin, scope, username="jack", email="Jack@Example.test",
        password="another-long-password", role=Role.VIEWER, request=req(),
    )  # fmt: skip
    assert new.email == "jack@example.test" and new.tenant_id == t.id
    with pytest.raises(Conflict):
        svc.create_user(
            session, admin, scope, username="jack", email="j2@example.test",
            password="another-long-password", role=Role.VIEWER, request=req(),
        )  # fmt: skip
    with pytest.raises(WeakPassword):
        svc.create_user(
            session, admin, scope, username="kim", email="k@example.test",
            password="short", role=Role.VIEWER, request=req(),
        )  # fmt: skip
    updated = svc.update_user(session, admin, scope, new.id, UserChanges(role=Role.ANALYST), req())
    assert updated.role == Role.ANALYST
    assert actions(session, resource_id=str(new.id)) == ["user.create", "user.update"]


def test_deactivate_revokes_tokens_and_unlock(
    session: Session, svc: AuthService, clock: FakeClock
) -> None:
    t = make_tenant(session)
    admin = make_user(session, t, "root", Role.ADMIN)
    u = make_user(session, t, "lee")
    pair = svc.login(session, t.slug, "lee", PASSWORD, req())
    svc.update_user(session, admin, TenantScope(t.id), u.id, UserChanges(is_active=False), req())
    with pytest.raises(TokenInvalid):
        svc.refresh(session, pair.refresh_token, req())
    with pytest.raises(TokenInvalid):
        svc.load_user(session, u.id, t.id)
    u.locked_until = clock.now + timedelta(minutes=5)
    u.failed_logins = 3
    session.flush()
    svc.update_user(
        session, admin, TenantScope(t.id), u.id, UserChanges(is_active=True, unlock=True), req()
    )
    assert u.locked_until is None and u.failed_logins == 0 and u.is_active


def test_admin_cannot_demote_or_deactivate_self(session: Session, svc: AuthService) -> None:
    t = make_tenant(session)
    admin = make_user(session, t, "root", Role.ADMIN)
    scope = TenantScope(t.id)
    with pytest.raises(PermissionDenied):
        svc.update_user(session, admin, scope, admin.id, UserChanges(role=Role.VIEWER), req())
    with pytest.raises(PermissionDenied):
        svc.update_user(session, admin, scope, admin.id, UserChanges(is_active=False), req())


def test_tenant_isolation(session: Session, svc: AuthService) -> None:
    a, b = make_tenant(session, "tenant-a"), make_tenant(session, "tenant-b")
    admin_a = make_user(session, a, "root", Role.ADMIN)
    victim = make_user(session, b, "secret-user")
    scope_a = TenantScope(a.id)
    assert [u.username for u in svc.list_users(session, scope_a)] == ["root"]
    with pytest.raises(NotFound):
        svc.get_user(session, scope_a, victim.id)
    with pytest.raises(NotFound):
        svc.update_user(session, admin_a, scope_a, victim.id, UserChanges(is_active=False), req())
    session.refresh(victim)
    assert victim.is_active
    # a token for tenant A cannot load a tenant-B user
    with pytest.raises(TokenInvalid):
        svc.load_user(session, victim.id, a.id)
    # created users always land in the actor's tenant
    created = svc.create_user(
        session, admin_a, scope_a, username="new", email="n@example.test",
        password="another-long-password", role=Role.VIEWER, request=req(),
    )  # fmt: skip
    assert created.tenant_id == a.id


def test_tenants(session: Session, svc: AuthService) -> None:
    home = make_tenant(session, "platform")
    sa = make_user(session, home, "super", Role.ADMIN, superadmin=True)
    t = svc.create_tenant(session, sa, slug="globex", name="Globex", request=req())
    assert "globex" in [x.slug for x in svc.list_tenants(session)]
    assert actions(session, resource_id=str(t.id)) == ["tenant.create"]
    with pytest.raises(Conflict):
        svc.create_tenant(session, sa, slug="globex", name="Again", request=req())
    with pytest.raises(Conflict, match="Slug"):
        svc.create_tenant(session, sa, slug="Bad Slug!", name="x", request=req())


def test_role_hierarchy() -> None:
    assert role_allows(Role.ADMIN, Role.VIEWER)
    assert role_allows(Role.ADMIN, Role.ANALYST)
    assert role_allows(Role.ANALYST, Role.VIEWER)
    assert not role_allows(Role.VIEWER, Role.ANALYST)
    assert not role_allows(Role.ANALYST, Role.ADMIN)
    assert role_allows(Role.VIEWER, Role.VIEWER)


def test_audit_helper_redacts_sensitive_keys(session: Session) -> None:
    from ai_platform_auth.audit import audit

    info = req("r-x")
    audit(session, "test.event", request=info, details={"password": "p", "refresh_token": "t",
                                                         "note": "ok"})  # fmt: skip
    row = session.scalar(select(AuditLog).where(AuditLog.action == "test.event"))
    assert row is not None
    assert row.details == {"password": "[redacted]", "refresh_token": "[redacted]", "note": "ok"}
    assert info.audited is True
