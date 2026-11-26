"""Tiny demo app showing the auth package and the three roles.

    set -a; source .env; set +a
    uv run python scripts/seed_demo.py
    uv run uvicorn --factory demo_app:create_app --app-dir core/auth --port 8000 \
        --log-level warning     # our JSON access log replaces uvicorn's

Also: http://127.0.0.1:8000/metrics, /healthz and /readyz.

Then open http://127.0.0.1:8000/docs, click "Authorize" and log in with username
``demo/demo-analyst`` (tenant/user) and that user's password.

No ``from __future__ import annotations`` here: FastAPI needs the real
``Depends(auth...)`` objects in endpoint signatures.
"""

from typing import Annotated

from ai_platform_auth import Auth, Role, TenantScope, User
from ai_platform_auth.db import migrate
from ai_platform_common.db import database_url, get_engine, make_session_factory
from ai_platform_observability import install_observability
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session, sessionmaker


def create_app(
    session_factory: sessionmaker[Session] | None = None, *, run_migrations: bool = True
) -> FastAPI:
    """Build the demo app. Refuses to start (ConfigError) without a valid JWT_SECRET."""
    if session_factory is None:
        url = database_url()
        if run_migrations:
            migrate(url)
        session_factory = make_session_factory(get_engine(url))
    auth = Auth.from_env(session_factory)

    app = FastAPI(title="ai-portfolio auth demo")
    auth.install(app)
    # logging, request context, access log, /metrics, /healthz, /readyz, AI-call tracing
    install_observability(app, session_factory, product="auth-demo",
                          trust_proxy_headers=auth.settings.trust_proxy_headers)  # fmt: skip

    @app.get("/demo/viewer", tags=["demo"])
    def viewer_area(
        user: Annotated[User, Depends(auth.require_role(Role.VIEWER))],
    ) -> dict[str, str]:
        """Any logged-in user (viewer, analyst or admin)."""
        return {"area": "viewer", "user": user.username, "role": user.role.value}

    @app.get("/demo/analyst", tags=["demo"])
    def analyst_area(
        user: Annotated[User, Depends(auth.require_role(Role.ANALYST))],
    ) -> dict[str, str]:
        """Analysts and admins."""
        return {"area": "analyst", "user": user.username, "role": user.role.value}

    @app.get("/demo/admin", tags=["demo"])
    def admin_area(
        user: Annotated[User, Depends(auth.require_role(Role.ADMIN))],
    ) -> dict[str, str]:
        """Admins only."""
        return {"area": "admin", "user": user.username, "role": user.role.value}

    @app.post("/demo/notes", tags=["demo"], status_code=201)
    def create_note(
        user: Annotated[User, Depends(auth.require_role(Role.ANALYST))],
        scope: Annotated[TenantScope, Depends(auth.get_tenant_scope)],
    ) -> dict[str, str]:
        """A write endpoint: the audit middleware records it automatically."""
        return {"saved_by": user.username, "tenant_id": str(scope.tenant_id)}

    return app
