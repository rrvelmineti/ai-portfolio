# core/auth — authentication, roles, tenants and audit log

Package: `ai_platform_auth` (in `src/`). Every product's FastAPI app mounts it the
same way. It provides login, short-lived JWT access tokens, rotating refresh tokens,
three tenant roles, a platform superadmin, and an append-only audit log in Postgres.

## Using it in a product

```python
from typing import Annotated

from ai_platform_auth import Auth, Role, TenantScope, User
from ai_platform_auth.db import migrate
from ai_platform_common.db import database_url, get_engine, make_session_factory
from fastapi import Depends, FastAPI

migrate(database_url())                                 # or run migrations in your deploy step
session_factory = make_session_factory(get_engine())    # DATABASE_URL
auth = Auth.from_env(session_factory)                   # raises ConfigError without a good JWT_SECRET

app = FastAPI()
auth.install(app)       # /auth/* router + error handlers + request-id and audit middleware

@app.get("/claims")
def list_claims(
    user: Annotated[User, Depends(auth.require_role(Role.ANALYST))],
    scope: Annotated[TenantScope, Depends(auth.get_tenant_scope)],
) -> list[dict]:
    # Filter every query by the tenant from the token: scope.apply(select(Claim), Claim)
    ...
```

> Create `auth` at module level. In a module with `from __future__ import annotations`,
> FastAPI resolves `Depends(auth...)` from module globals, so it can't find an `auth`
> created inside a function.

### Dependencies

| Dependency | Gives you | Fails with |
|---|---|---|
| `auth.get_current_user` | The active `User` behind the bearer token. It's reloaded on every request, so deactivation and role changes apply immediately. | 401 |
| `auth.require_role("viewer" \| "analyst" \| "admin")` | That user, if their role is high enough. The hierarchy is **admin > analyst > viewer**. | 401 / 403 |
| `auth.require_superadmin` | A platform superadmin user. | 401 / 403 |
| `auth.get_tenant_scope` | `TenantScope(tenant_id)`, always taken **from the token**. | 401 |
| `auth.get_session` | A request-scoped SQLAlchemy session. Use `scope="function"`. | |

## API (`/auth`)

| Method and path | Who | Notes |
|---|---|---|
| `POST /auth/login` | anyone | OAuth2 password form with fields `username`, `password` and `tenant`, or `username=tenant/user` (used by Swagger "Authorize"). Returns `access_token`, `refresh_token`, `token_type`, `expires_in`. Rate-limited per IP. |
| `POST /auth/refresh` | holder of a refresh token | Body `{"refresh_token": ...}`. Rotates the token. |
| `POST /auth/logout` | holder of a refresh token | Revokes that login's whole token family. Returns 204. |
| `GET /auth/me` | logged in | The current user. |
| `POST /auth/users` | admin | Create a user in **your** tenant. |
| `GET /auth/users` | admin | List your tenant's users. |
| `PATCH /auth/users/{id}` | admin | `role`, `is_active`, `unlock`. You can't demote or deactivate yourself. |
| `POST /auth/tenants`, `GET /auth/tenants` | superadmin | Manage tenants (`slug`, `name`). |

Errors return `{"detail": ...}` with status 401, 403, 404, 409, 422 or 429.
- Every failed login returns the same `401 Invalid credentials`, whatever the cause: unknown tenant, unknown user, wrong password, locked account or inactive account. The real reason goes only to the audit log.
- Another tenant's resources return **404**, so the response never reveals that they exist.

## Security model

- **Passwords:** Argon2id (pwdlib), 12–128 characters, no other complexity rules
  (NIST SP 800-63B). Hashes are upgraded on login. An unknown username still costs
  one dummy hash check, so response time doesn't reveal whether the user exists.
- **Access tokens:** JWT HS256, valid 15 minutes, with claims `sub`, `tenant_id`,
  `role`, `jti`, `iat` and `exp`. Decoding requires every claim and accepts only HS256.
  The key comes from `JWT_SECRET`, which must be at least 32 bytes; the app refuses to
  start otherwise. There's no default secret.
- **Refresh tokens:**
  - 256 random bits; the database stores only their SHA-256 hash. Valid 7 days.
  - **Rotated on every use.** Each login starts a *family*, and each refresh revokes
    the old token and sets `replaced_by` to the new one.
  - **Presenting a revoked token revokes the whole family.** This signals possible
    token theft, and it's audited as `auth.refresh_reuse_detected`.
- **Lockout:** 5 failed logins lock the account for 15 minutes. A successful login
  resets the counter, and an admin can unlock early with `PATCH {"unlock": true}`.
- **Rate limit:** `POST /auth/login` allows 10 attempts per minute per IP (429 with
  `Retry-After`).
  - The limiter is behind the `RateLimiter` protocol. The in-memory version is right
    for one instance; swap in Redis for several.
  - `X-Forwarded-For` is ignored unless `TRUST_PROXY_HEADERS=true`.
- **Tenant isolation:** query helpers take a `TenantScope` built from the verified
  token, never a tenant id from the request body. Tests cover reading, listing,
  updating and creating users across tenants.
- **Audit log** (`auth.audit_log`): append-only.
  - The code only inserts, and a database trigger rejects `UPDATE`, `DELETE` and
    `TRUNCATE`.
  - Each row records `request_id`, IP, user agent, tenant and user.
  - Sensitive keys in `details` (password, tokens, secret) are redacted.
  - `AuditMiddleware` writes one `http.<method>` row for every write request that
    wasn't audited explicitly. If an audit write fails, the response isn't affected.
- **Logging:** structured JSON only. Passwords, tokens, hashes and `JWT_SECRET` are
  never logged, and tests check this.

Recorded actions:
- **Login and tokens:** `auth.login`, `auth.login_failed`, `auth.lockout`,
  `auth.login_rate_limited`, `auth.refresh`, `auth.refresh_reuse_detected`, `auth.logout`.
- **Admin changes:** `user.create`, `user.update`, `tenant.create`.
- **Other writes:** `http.post`, `http.patch`, etc.

## Data model (Postgres schema `auth`)

| Table | Columns |
|---|---|
| `tenants` | `id`, `slug`, `name`, `created_at` |
| `users` | `id`, `tenant_id`, `username` (unique per tenant), `email`, `password_hash`, `role` (enum), `is_superadmin`, `is_active`, `failed_logins`, `locked_until`, `created_at`, `last_login_at` |
| `refresh_tokens` | `id`, `user_id`, `family_id`, `token_hash`, `created_at`, `expires_at`, `revoked_at`, `replaced_by` |
| `audit_log` | `id`, `tenant_id`, `user_id`, `action`, `resource_type`, `resource_id`, `ip`, `user_agent`, `request_id`, `details` (JSONB), `created_at` |

Migrations: Alembic in `src/ai_platform_auth/migrations`, with its own version table
(`auth.alembic_version_auth`). Run them with `ai_platform_auth.db.migrate(url)`.

> **Production note:** the trigger stops the application from changing the audit log.
> A database owner can still disable triggers. Run the app as a role that has only
> `INSERT, SELECT` on `audit_log` and doesn't own the table; this belongs to the
> infrastructure phase.

## Configuration

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | none | `postgresql+psycopg://...` |
| `JWT_SECRET` | none, **required** | at least 32 bytes |
| `ACCESS_TOKEN_TTL_MINUTES` | 15 | |
| `REFRESH_TOKEN_TTL_DAYS` | 7 | |
| `MAX_FAILED_LOGINS` / `LOCKOUT_MINUTES` | 5 / 15 | |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW_SECONDS` | 10 / 60 | |
| `TRUST_PROXY_HEADERS` | false | only behind a trusted proxy |

## Local demo

```bash
cp .env.example .env                 # fill POSTGRES_PASSWORD, JWT_SECRET, DEMO_*_PASSWORD, URLs
docker compose up -d db
set -a; source .env; set +a
uv run python scripts/seed_demo.py   # tenant "demo": demo-admin, demo-analyst, demo-viewer
uv run uvicorn --factory demo_app:create_app --app-dir core/auth --port 8000
```

Open `http://127.0.0.1:8000/docs`, click **Authorize**, and log in as
`demo/demo-viewer`, `demo/demo-analyst` or `demo/demo-admin`. Then try `/demo/viewer`,
`/demo/analyst`, `/demo/admin` and `POST /demo/notes`.

## Tests

Database tests need Postgres. They create and migrate a throwaway database from
`TEST_DATABASE_URL`, and run each test inside a transaction that is rolled back.

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/postgres uv run pytest core/auth
```

Without `TEST_DATABASE_URL` they're skipped locally. CI sets `REQUIRE_DB_TESTS=1`, so
there they must run.
