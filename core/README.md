# core

Shared platform services used by every product in this monorepo. Nothing in here
is product-specific: products depend on the platform, never the other way round.

| Folder | Purpose |
|---|---|
| `gateway/` | Model gateway: the only way products call AI models (`ai_platform_gateway`) |
| `auth/` | Authentication, roles, tenants and append-only audit log (`ai_platform_auth`) |
| `observability/` | Request context wiring, access log, `llm_calls`, LangSmith, Prometheus, health (`ai_platform_observability`) |
| `guardrails/` | PII masking (Presidio), prompt-injection defence, topic policy, approvals (`ai_platform_guardrails`) |
| `common/` | Shared utilities: env config, request context, JSON logging + redaction, DB helpers |

> Note: this folder is **not** a Python package. Each sub-folder ships its own
> package, e.g. `common/src/ai_platform_common`. It was called `platform/` until
> Phase 0.3; see `docs/decisions/0001-monorepo-layout-and-tooling.md` for why it
> was renamed.
