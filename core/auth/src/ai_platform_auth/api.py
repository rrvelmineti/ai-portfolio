"""FastAPI integration: ``Auth`` bundles the router, dependencies and middleware.

from ai_platform_auth import Auth

auth = Auth.from_env(session_factory)    # refuses to start without a valid JWT_SECRET
auth.install(app)                        # router + error handlers + middleware

@app.get("/reports", dependencies=[Depends(auth.require_role("analyst"))])
def reports(scope: TenantScope = Depends(auth.get_tenant_scope)) -> ...: ...

No ``from __future__ import annotations`` in this module: FastAPI must see the real
``Annotated[..., Depends(...)]`` objects of the dependencies built inside methods.
"""

import uuid
from collections.abc import Callable, Iterator
from typing import Annotated

from ai_platform_common.context import current, ensure_request_context, set_user
from fastapi import APIRouter, Depends, FastAPI, Form, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_auth.audit import audit
from ai_platform_auth.errors import (
    AuthError,
    Conflict,
    InvalidCredentials,
    NotFound,
    PermissionDenied,
    RateLimitExceeded,
    TokenInvalid,
    WeakPassword,
)
from ai_platform_auth.middleware import AuditMiddleware
from ai_platform_auth.models import Role, User
from ai_platform_auth.ratelimit import InMemoryRateLimiter, RateLimiter
from ai_platform_auth.schemas import (
    RefreshRequest,
    TenantCreate,
    TenantOut,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from ai_platform_auth.scope import TenantScope
from ai_platform_auth.service import AuthService, Clock, UserChanges, role_allows, utcnow
from ai_platform_auth.settings import AuthSettings
from ai_platform_auth.tokens import decode_access_token

_STATUS: dict[type[AuthError], int] = {
    InvalidCredentials: status.HTTP_401_UNAUTHORIZED,
    TokenInvalid: status.HTTP_401_UNAUTHORIZED,
    PermissionDenied: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,
    WeakPassword: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RateLimitExceeded: status.HTTP_429_TOO_MANY_REQUESTS,
}


def auth_error_response(_request: Request, exc: Exception) -> JSONResponse:
    """Map auth errors to HTTP responses with client-safe messages."""
    code = next((c for t, c in _STATUS.items() if isinstance(exc, t)), 400)
    headers: dict[str, str] = {}
    if code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if isinstance(exc, RateLimitExceeded):
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse({"detail": str(exc)}, status_code=code, headers=headers)


def _mark_audited() -> None:
    """Tell the audit middleware this request needs no generic audit row."""
    ctx = current()
    if ctx is not None:
        ctx.audited = True


class Auth:
    """Everything a product needs to add authentication to a FastAPI app."""

    def __init__(
        self,
        settings: AuthSettings,
        session_factory: sessionmaker[Session],
        *,
        rate_limiter: RateLimiter | None = None,
        clock: Clock = utcnow,
        prefix: str = "/auth",
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.clock = clock
        self.service = AuthService(settings, clock=clock)
        self.rate_limiter: RateLimiter = rate_limiter or InMemoryRateLimiter(
            settings.login_rate_limit, settings.login_rate_window_s
        )
        self.oauth2 = OAuth2PasswordBearer(tokenUrl=f"{prefix}/login", auto_error=False)
        self.router = self._build_router(prefix)

    @classmethod
    def from_env(
        cls,
        session_factory: sessionmaker[Session],
        *,
        rate_limiter: RateLimiter | None = None,
        clock: Clock = utcnow,
    ) -> "Auth":
        """Build from environment settings; raises ``ConfigError`` on a bad JWT_SECRET."""
        return cls(AuthSettings.from_env(), session_factory, rate_limiter=rate_limiter, clock=clock)

    def install(self, app: FastAPI) -> None:
        """Mount the router, register error handlers and add the middleware."""
        app.include_router(self.router)
        app.add_exception_handler(AuthError, auth_error_response)
        app.add_middleware(AuditMiddleware, session_factory=self.session_factory)
        # shared request context (request id, ip, user) as the outermost middleware
        ensure_request_context(app, trusted_proxies=self.settings.trusted_proxies)

    # ------------------------------------------------------------------ dependencies
    def get_session(self) -> Iterator[Session]:
        """One session per request. Use with ``Depends(auth.get_session, scope="function")``
        so the connection is released as soon as the endpoint returns."""
        with self.session_factory() as session:
            yield session

    @property
    def get_current_user(self) -> Callable[..., User]:
        """Dependency: the active user behind the bearer token (401 otherwise)."""
        oauth2 = self.oauth2
        service = self.service
        settings = self.settings

        def dependency(
            token: Annotated[str | None, Depends(oauth2)],
            session: Annotated[Session, Depends(self.get_session, scope="function")],
        ) -> User:
            if not token:
                raise TokenInvalid("Not authenticated")
            claims = decode_access_token(token, settings)
            user = service.load_user(session, claims.user_id, claims.tenant_id)
            set_user(user.tenant_id, user.id)
            return user

        return dependency

    def require_role(self, role: Role | str) -> Callable[..., User]:
        """Dependency factory enforcing ``admin > analyst > viewer``."""
        required = Role(role)
        current = self.get_current_user

        def dependency(user: Annotated[User, Depends(current)]) -> User:
            if not role_allows(user.role, required):
                raise PermissionDenied(f"Requires role {required.value}")
            return user

        return dependency

    @property
    def require_superadmin(self) -> Callable[..., User]:
        current = self.get_current_user

        def dependency(user: Annotated[User, Depends(current)]) -> User:
            if not user.is_superadmin:
                raise PermissionDenied("Requires platform superadmin")
            return user

        return dependency

    @property
    def get_tenant_scope(self) -> Callable[..., TenantScope]:
        """Dependency: the caller's tenant, taken from the verified token."""
        current = self.get_current_user

        def dependency(user: Annotated[User, Depends(current)]) -> TenantScope:
            return TenantScope(user.tenant_id)

        return dependency

    # ------------------------------------------------------------------ router
    def _build_router(self, prefix: str) -> APIRouter:
        router = APIRouter(prefix=prefix, tags=["auth"])
        svc = self.service
        SessionDep = Annotated[Session, Depends(self.get_session, scope="function")]  # noqa: N806
        AdminDep = Annotated[User, Depends(self.require_role(Role.ADMIN))]  # noqa: N806
        CurrentDep = Annotated[User, Depends(self.get_current_user)]  # noqa: N806
        SuperDep = Annotated[User, Depends(self.require_superadmin)]  # noqa: N806

        @router.post("/login", response_model=TokenResponse)
        def login(
            session: SessionDep,
            username: Annotated[str, Form(max_length=200)],
            password: Annotated[str, Form(max_length=256)],
            tenant: Annotated[str | None, Form(max_length=63)] = None,
        ) -> TokenResponse:
            """OAuth2 password login. Give ``tenant``, or ``username`` as ``tenant/user``."""
            ctx = current()
            try:
                self.rate_limiter.hit(f"login:{(ctx.ip if ctx else None) or 'unknown'}")
            except RateLimitExceeded:
                audit(session, "auth.login_rate_limited", details={"tenant": tenant})
                session.commit()
                raise
            if tenant is None and "/" in username:
                tenant, username = username.split("/", 1)
            pair = svc.login(session, tenant or "", username, password)
            return TokenResponse(
                access_token=pair.access_token,
                refresh_token=pair.refresh_token,
                expires_in=pair.expires_in,
            )

        @router.post("/refresh", response_model=TokenResponse)
        def refresh(body: RefreshRequest, session: SessionDep) -> TokenResponse:
            pair = svc.refresh(session, body.refresh_token)
            return TokenResponse(
                access_token=pair.access_token,
                refresh_token=pair.refresh_token,
                expires_in=pair.expires_in,
            )

        @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
        def logout(body: RefreshRequest, session: SessionDep) -> None:
            svc.logout(session, body.refresh_token)
            _mark_audited()  # unknown tokens: nothing to record beyond the request

        @router.get("/me", response_model=UserOut)
        def me(user: CurrentDep) -> UserOut:
            return UserOut.from_user(user, self.clock())

        @router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
        def create_user(body: UserCreate, admin: AdminDep, session: SessionDep) -> UserOut:
            user = svc.create_user(
                session, admin, TenantScope(admin.tenant_id), username=body.username,
                email=str(body.email), password=body.password, role=body.role,
            )  # fmt: skip
            return UserOut.from_user(user, self.clock())

        @router.get("/users", response_model=list[UserOut])
        def list_users(admin: AdminDep, session: SessionDep) -> list[UserOut]:
            now = self.clock()
            users = svc.list_users(session, TenantScope(admin.tenant_id))
            return [UserOut.from_user(u, now) for u in users]

        @router.patch("/users/{user_id}", response_model=UserOut)
        def update_user(
            user_id: uuid.UUID, body: UserUpdate, admin: AdminDep, session: SessionDep
        ) -> UserOut:
            user = svc.update_user(
                session, admin, TenantScope(admin.tenant_id), user_id,
                UserChanges(role=body.role, is_active=body.is_active, unlock=body.unlock),
            )  # fmt: skip
            _mark_audited()  # no-op changes need no extra row
            return UserOut.from_user(user, self.clock())

        @router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
        def create_tenant(body: TenantCreate, actor: SuperDep, session: SessionDep) -> TenantOut:
            tenant = svc.create_tenant(session, actor, slug=body.slug, name=body.name)
            return TenantOut.from_tenant(tenant)

        @router.get("/tenants", response_model=list[TenantOut])
        def list_tenants(_actor: SuperDep, session: SessionDep) -> list[TenantOut]:
            return [TenantOut.from_tenant(t) for t in svc.list_tenants(session)]

        return router
