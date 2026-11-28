"""The claims onboarding API (Spec 1).

    set -a; source .env; set +a
    uv run uvicorn --factory claims_onboarding.app:create_app --port 8001 --log-level warning

Mounts the shared platform through each component's ``install()``:

- auth: ``/auth/*`` login, roles, tenants, the audit middleware;
- observability: request IDs, JSON access log, ``/metrics``, ``/healthz``, ``/readyz``;
- guardrails: PII masking and injection defence on every AI call, ``/approvals``.

Migrations never run at startup unless ``RUN_MIGRATIONS_ON_START=true`` (local only);
deployments migrate from CI with the owner role (ADR 0008).
"""

from __future__ import annotations

from ai_platform_auth import Auth
from ai_platform_auth.db import migrate as migrate_auth
from ai_platform_common.config import get_bool
from ai_platform_common.db import database_url, get_engine, make_session_factory
from ai_platform_guardrails import install_guardrails
from ai_platform_guardrails.db import migrate as migrate_guardrails
from ai_platform_observability import install_observability
from ai_platform_observability.db import migrate as migrate_observability
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from claims_onboarding.api import build_router
from claims_onboarding.db import migrate as migrate_claims

PRODUCT = "claims-onboarding"


def create_app(
    session_factory: sessionmaker[Session] | None = None, *, run_migrations: bool | None = None
) -> FastAPI:
    """Build the app. Refuses to start (ConfigError) without a valid JWT_SECRET."""
    if run_migrations is None:
        run_migrations = get_bool("RUN_MIGRATIONS_ON_START", False)
    if session_factory is None:
        url = database_url()
        if run_migrations:
            for migrate in (migrate_auth, migrate_observability, migrate_guardrails,
                            migrate_claims):  # fmt: skip
                migrate(url)
        session_factory = make_session_factory(get_engine(url))
    auth = Auth.from_env(session_factory)

    app = FastAPI(title="Claims onboarding", version="0.1.0")
    auth.install(app)
    install_observability(app, session_factory, product=PRODUCT,
                          trusted_proxies=auth.settings.trusted_proxies)  # fmt: skip
    install_guardrails(app, auth, session_factory)
    app.include_router(build_router(auth))
    return app
