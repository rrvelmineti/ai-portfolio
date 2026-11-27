# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Phase 0.5: Guardrails
- `ai_platform_guardrails` (`core/guardrails`):
  - **Fail closed:** if a guardrail errors, the AI call is blocked.
  - **PII masking** with Presidio and `en_core_web_lg`:
    - custom recognizers: MEMBER_ID, CLAIM_NUMBER, POLICY_NUMBER, NPI (Luhn),
      DOB-in-context, street addresses, US phone formats, Luhn card numbers, bank
      accounts; per-tenant YAML patterns;
    - typed, consistent placeholders; `unmask()` from a request-scoped vault that is
      never logged;
    - `mask_records()` and `privacy_report()`; HIPAA Safe Harbor mode.
  - **Layered injection defence:**
    - normalisation and decoding, weighted rules (6 languages), an optional LLM
      classifier;
    - `wrap_untrusted()` fences, where content is flagged rather than blocked;
    - a canary token, blocking of exfiltration links, a tool allowlist;
    - `guardrail.blocked` / `guardrail.flagged` audit rows.
  - **Per-product topic policy** with refusals (example policies for Rewards Advisor and
    Meal Vision).
  - **Approval gate:** `guardrails.approval_requests`, four-eyes, expiry,
    role-based visibility, `/approvals` router, audited decisions.
  - `install_guardrails()` / `configure_guardrails()`.
- Gateway:
  - `ai_platform_gateway.guards` (Guard protocol, `PassthroughGuard` for unit tests);
  - `pii` = mask / block / off (off requires a logged `pii_reason`) and `injection` =
    block / flag / off;
  - result fields `mapping_id`, `pii_entities`, `injection_score`, `guard_flags`,
    `output_flags`;
  - `GuardrailError`, `GuardrailBlocked`, `PolicyViolation`.
- Observability: LangSmith content always goes through the PII masker first.
- Evaluation:
  - synthetic corpus in `eval/`: 190 texts, a 40-row claims table, 35 attacks and 35
    benign look-alikes;
  - `scripts/guardrails_report.py` and `docs/guardrails-results.md`;
  - a CI gate on the targets.
- Demo: `/demo/refunds` creates an approval request; the seed adds `demo-reviewer` as a
  second approver.
- Docs: ADR 0005, `docs/ai-use-policy.md`, `core/guardrails/README.md`.

### Fixed — Phase 0.5
- `presidio-analyzer` DEBUG logs contained analysed text; that logger is capped at
  WARNING.

### Added — Phase 0.4: Observability
- `ai_platform_common.context`: per-request `RequestContext` (request_id, tenant, user,
  route template, product) and a pure-ASGI middleware that reuses or creates
  `X-Request-ID` and echoes it in the response.
- Logging:
  - JSON lines with the request context on every line;
  - key-based `redact()` / `RedactionFilter` (password, token, secret,
    authorization, api_key, cookie, ...), with token counts kept;
  - uvicorn, FastAPI and SQLAlchemy logs routed through it.
- `ai_platform_observability` (`core/observability`):
  - `install_observability(app, session_factory, product=...)`;
  - one `http.access` line per request (route template, status, latency);
  - Prometheus `/metrics`, `/healthz`, `/readyz` (503 when the DB is down);
  - optional Sentry with no-PII data collection and redaction.
- AI-call tracing:
  - one `observability.llm_calls` row per call, with cost and latency and no text
    (Alembic migration);
  - LLM metrics;
  - optional LangSmith runs: metadata only, content behind `LANGSMITH_LOG_CONTENT`
    and the `redact_content()` hook;
  - `traced()` groups workflow steps;
  - tracing failures never break an AI call.
- Gateway: `complete()`/`embed()` take the request id, tenant, user and product from
  the context; prompt/response text reaches hooks only when capture is enabled.
- `scripts/usage_report.py` (cost, calls, avg/p95 latency and fallback rate per
  product, provider and day) and `scripts/smoke_observability.py` (live).
- ADR 0004 and `core/observability/README.md`.

### Changed — Phase 0.4
- Auth uses the shared request context: `RequestInfo` and auth's request-id middleware
  are removed; `audit()` takes request details from the context; `AuditMiddleware` is
  pure ASGI.
- `ai_platform_common.testing` holds the shared Postgres test-database helpers.
- `fake_gateway()` treats providers it wasn't given as unconfigured.

### Added — Phase 0.3: Authentication, roles and audit log
- `ai_platform_auth` (`core/auth`), mounted by any FastAPI product with `Auth.install(app)`:
  - Endpoints: `/auth/login` (OAuth2 form plus tenant), `/refresh`, `/logout`, `/me`;
    admin `/users` (create, list, role, deactivate, unlock); superadmin `/tenants`.
  - Dependencies: `get_current_user`, `require_role` (admin > analyst > viewer),
    `require_superadmin`, `get_tenant_scope`.
- Postgres schema `auth` with Alembic migrations:
  - `tenants`, `users` and `refresh_tokens`;
  - an append-only `audit_log`, with a trigger that blocks UPDATE, DELETE and TRUNCATE.
- Security:
  - Argon2id passwords (12+ characters);
  - HS256 access tokens valid 15 minutes, with a startup check that `JWT_SECRET`
    is at least 32 bytes;
  - rotating 7-day refresh tokens with reuse detection that revokes the whole family;
  - lockout after 5 failures for 15 minutes, with one generic login error;
  - per-IP login rate limiting behind a `RateLimiter` interface;
  - tenant isolation through `TenantScope`.
- `audit()` helper, request-ID middleware, and audit middleware for every write request.
- `ai_platform_common.db`: engine, sessions, Alembic migrations runner.
- `docker-compose.yml` (Postgres 16 with pgvector); a CI Postgres service container,
  with database tests required in CI.
- `scripts/seed_demo.py` (demo tenant and users, passwords from env vars) and
  `core/auth/demo_app.py` (one endpoint per role).
- ADR 0003 and `core/auth/README.md`.

### Changed — Phase 0.3 cleanup
- Renamed the top-level `platform/` folder to `core/` (package names unchanged) and
  removed the pytest stdlib-`platform` workaround; the regression test stays.
  See the ADR 0001 revision note.
- Gateway: the `gpt-4o-mini` alias is now `openai-mini` and routes to `gpt-6-luna`,
  OpenAI's named replacement for the deprecated `gpt-4o-mini`. The `fast` chain
  ends in `gpt-6-luna` as well.

### Added — Phase 0.2: Model gateway
- `ai_platform_gateway` (`platform/gateway`): `complete()` / `embed()`, the single
  entry point for AI model calls, with `CompletionResult` / `EmbeddingResult`.
- Provider adapters on official SDKs: Anthropic, OpenAI, Azure OpenAI, AWS Bedrock
  (boto3 Converse + Titan embeddings), Google Gemini (google-genai), Groq.
- `models.yaml`: model catalog and aliases (`default`, `best`, `fast`, `vision`,
  `default-embed`, `titan-embed`, `claude-sonnet`, `gpt-4o-mini`, `gemini-flash`,
  `groq-llama`) with ordered fallback chains, prices, context limits and
  capability flags (`supports_temperature`, `supports_images`, `supported_dimensions`).
- Reliability:
  - per-call timeouts;
  - tenacity retries (3 attempts, exponential backoff + jitter) on 429/5xx,
    timeouts and connection errors only;
  - failover across providers, skipping unconfigured or incapable entries;
  - gateway exception hierarchy, including `NoProvidersConfigured` and
    `AllProvidersFailed` (with its attempt list).
- Structured output via `response_schema` (pydantic validation, one repair retry).
- Cost tracking from confirmed prices only. Unverified prices give
  `cost_usd = null` and `cost_unknown=true`.
- One structured JSON log line per call (no prompt text unless `DEBUG_LOG_PROMPTS`),
  plus the `on_call_complete` hook list for Phase 0.4 tracing.
- `langchain_adapter.py`: `GatewayChatModel` and `GatewayEmbeddings`.
- `ai_platform_gateway.testing`: fake providers for product unit tests.
- `ai_platform_common.config` (env-var helpers) and `ai_platform_common.logging`
  (JSON formatter).
- `scripts/smoke_gateway.py` live smoke table, and the `live` pytest marker
  (deselected by default).
- Gateway env vars in `.env.example`; ADR 0002.

### Fixed — Phase 0.2
- pytest `--import-mode=importlib` replaced the stdlib `platform` module with the
  repo's `platform/` folder during test runs, which broke botocore and truststore.
  A root `conftest.py` now prevents this, and a regression test guards it.


### Added — Phase 0.1: Platform foundation
- `CLAUDE.md` with project goal, stack, coding and workflow rules.
- Monorepo folder structure (`platform/`, `products/`, `infra/terraform`,
  `.github/workflows`, `docs/decisions`) with a README in each folder.
- uv workspace on Python 3.12 with lockfile (`uv.lock`).
- Ruff (lint + format), strict mypy, pytest with an 80% coverage gate.
- Pre-commit hooks (Ruff, mypy, whitespace/YAML/TOML checks, private-key detection).
- GitHub Actions CI running lint, format check, type check and tests on every PR.
- Sample package `ai_platform_common` with tests.
- `docs/plan.md` and ADR 0001.
