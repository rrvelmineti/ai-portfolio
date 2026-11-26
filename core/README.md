# core

Shared platform services used by every product in this monorepo. Nothing in here
is product-specific: products depend on the platform, never the other way round.

| Folder | Purpose |
|---|---|
| `gateway/` | Model gateway: the only way products call AI models (`ai_platform_gateway`) |
| `auth/` | Authentication and authorization |
| `observability/` | Structured JSON logging, tracing, metrics, LangSmith |
| `guardrails/` | PII detection/redaction (Presidio) and LLM input/output safety |
| `common/` | Shared utilities: env config, JSON logging, base types |

> Note: this folder is **not** a Python package. Each sub-folder ships its own
> package, e.g. `common/src/ai_platform_common`. It was called `platform/` until
> Phase 0.3; see `docs/decisions/0001-monorepo-layout-and-tooling.md` for why it
> was renamed.
