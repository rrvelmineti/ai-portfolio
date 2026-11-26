# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
