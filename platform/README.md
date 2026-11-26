# platform

Shared platform services used by every product in this monorepo. Nothing in here
is product-specific: products depend on the platform, never the other way round.

| Folder | Purpose |
|---|---|
| `gateway/` | API gateway: routing, rate limiting, request validation |
| `auth/` | Authentication and authorization |
| `observability/` | Structured JSON logging, tracing, metrics, LangSmith |
| `guardrails/` | PII detection/redaction (Presidio) and LLM input/output safety |
| `common/` | Shared utilities, config loading, base types |

> Note: this folder is **not** a Python package (it would shadow the stdlib
> `platform` module). Each sub-folder ships its own package, e.g.
> `common/src/ai_platform_common`. See `docs/decisions/0001-monorepo-layout-and-tooling.md`.
