# core/observability: logs, request tracing, AI call tracing, metrics, health

Package: `ai_platform_observability` (in `src/`). One call wires everything into a
FastAPI app. Every integration is a **no-op when its env vars are missing**, so local
dev and CI need no accounts.

```python
from ai_platform_observability import install_observability, traced

app = FastAPI()
auth.install(app)  # optional, see core/auth
install_observability(app, session_factory, product="claims-onboarding")


@app.post("/files/{file_id}/map")
def map_file(file_id: str) -> dict:
    with traced("map file", metadata={"file_id": file_id}):  # groups the AI calls below
        fields = complete("...", model="default")  # request_id picked up automatically
        ...
```

For scripts and workers without a web app:

```python
from ai_platform_common.context import request_context
from ai_platform_observability import setup_tracing

setup_tracing(session_factory)
with request_context(product="claims-onboarding"):
    complete("...")
```

## What you get

| Piece | Where | Off unless… |
|---|---|---|
| Request context: `request_id`, `tenant_id`, `user_id`, route, product | `ai_platform_common.context` | always on |
| JSON logs with the context on every line, plus redaction | `ai_platform_common.logging` | always on |
| One `http.access` line per request (method, **route template**, status, `latency_ms`) | `ObservabilityMiddleware` | always on |
| One `observability.llm_calls` row per AI call | `LlmCallRecorder` | a `session_factory` is given |
| Prometheus `/metrics`, `/healthz`, `/readyz` | `build_ops_router` | always on |
| LangSmith runs (metadata only) | `LangSmithTracer` | `LANGSMITH_API_KEY` |
| LangSmith prompt/response text, after `redact_content()` | same | also `LANGSMITH_LOG_CONTENT=true` |
| Sentry errors | `init_sentry` | `SENTRY_DSN` |

### Request context
- **`X-Request-ID`:** a safe incoming value (≤ 100 characters, `[A-Za-z0-9._:-]`) is
  reused; anything else is replaced with a UUID. The value is echoed on the response.
- **Where it's used:**
  - auth's `get_current_user` adds `tenant_id` and `user_id`;
  - the gateway (`complete()`/`embed()`), the audit log, `llm_calls` and every log line
    all read the same context.
- **Implementation:** pure ASGI middleware, so values set inside the endpoint are
  visible to the access log and audit middleware afterwards. Log records are stamped
  with the context when they're created.

### Logging and redaction
`configure_logging()` (called by `install_observability`):
- routes uvicorn, FastAPI, SQLAlchemy and Alembic logs to one JSON handler;
- turns off uvicorn's own access log;
- caps SQLAlchemy engine logs at WARNING, so SQL parameters are never logged.

Each line has `timestamp`, `level`, `logger`, `message`, `request_id`, `tenant_id`,
`user_id`, plus any `extra={"fields": {...}}`.

`redact()` replaces the value of any key whose parts (split on `_ - .` and spaces)
include `password`, `passwd`, `pwd`, `token`, `secret`, `authorization`, `cookie(s)`,
`apikey`, `jwt`, `dsn`, `credential(s)` or `passphrase`, or that contains `api_key`,
`private_key`, `access_key` or `session_id`.
- It works at any depth, including header lists.
- Counts such as `input_tokens` are kept.
- It's used for logs, Sentry events and audit details.

### AI calls: the `llm_calls` table
One row per `complete()`/`embed()`, on success and on failure.

| Columns | |
|---|---|
| Who and where | `request_id`, `tenant_id`, `user_id`, `product`, `operation`, `alias` |
| What answered | `provider`, `model`, `attempts`, `fallback_used` |
| Usage and cost | `input_tokens`, `output_tokens`, `cost_usd` (null when the price is unverified), `cost_unknown`, `latency_ms` |
| Outcome | `status`, `error_type`, `created_at` |

- **No prompt or response text, ever.**
- **Product:** `metadata={"product": ...}` on the call overrides the context's product.
- **Failures:** the recorder, metrics and LangSmith steps are isolated. A failure logs
  `observability.record_failed` and the AI call still succeeds.

### LangSmith
- Each AI call becomes an `llm` (or `embedding`) run. Its metadata covers alias,
  provider, model, tokens, cost, attempts, fallback, `request_id`, tenant, user and
  product.
- `traced("name")` (context manager or decorator, sync or async) creates a parent
  `chain` run, and the AI calls inside it become its child runs. Without LangSmith it
  writes a `trace.span` log line with the duration instead.
- **Content is off by default.** With `LANGSMITH_LOG_CONTENT=true`, the gateway
  attaches prompt and response to the call record, and it's sent only after
  `redact_content()`.
- `set_content_redactor(fn)` is the hook Phase 0.5 (Presidio) will plug into.

### Metrics (`/metrics`)
- **HTTP:** `http_requests_total{method,route,status}`,
  `http_request_duration_seconds{method,route}`.
- **AI calls:**
  - `llm_calls_total{provider,model,status}`;
  - `llm_tokens_total{provider,model,direction}`;
  - `llm_cost_usd_total{provider,model}`;
  - `llm_cost_unknown_total{provider,model}`;
  - `llm_call_duration_seconds{provider,model}`;
  - `llm_fallbacks_total{alias}`.

Routes are templates, never raw paths, so IDs never become labels.
`/metrics`, `/healthz` and `/readyz` are unauthenticated, for scrapers and probes on a
private network. `/readyz` returns 503 when `SELECT 1` fails.

### Sentry
Initialised only with `SENTRY_DSN`.
- sentry-sdk 3.x replaced `send_default_pii`, so the equivalent is set through
  `data_collection`: no user info, cookies, bodies, AI inputs/outputs or frame
  variables.
- `before_send` and `before_breadcrumb` apply the same `redact()`.

## Usage report

```bash
uv run python scripts/usage_report.py                       # 30 days, by product/provider/day
uv run python scripts/usage_report.py --by product --days 7
uv run python scripts/usage_report.py --by product,model --product claims-onboarding --format csv
```

| Column | |
|---|---|
| `calls` | AI calls in the period |
| `cost_usd` | total known cost |
| `avg_cost` | average per priced call, i.e. cost per answer or per file |
| `unpriced` | calls whose model has no verified price |
| `avg_ms`, `p95_ms` | latency |
| `fallback%`, `error%` | rates |
| `tok_in`, `tok_out` | tokens |

## Configuration

| Variable | Default | |
|---|---|---|
| `LANGSMITH_API_KEY` | unset (LangSmith off) | |
| `LANGSMITH_PROJECT` | `ai-portfolio-dev` | |
| `LANGSMITH_ENDPOINT` | LangSmith default | e.g. the EU endpoint |
| `LANGSMITH_LOG_CONTENT` | `false` | send prompt/response (after redaction) |
| `SENTRY_DSN` | unset (Sentry off) | |
| `APP_ENV` | none | Sentry environment |
| `LOG_LEVEL` | `INFO` | |

## Migrations and checks

- `ai_platform_observability.db.migrate(url)` creates the `observability` schema and its
  `llm_calls` table, with version table `alembic_version_observability`.
- Live check (not in CI): `uv run python scripts/smoke_observability.py` or
  `uv run pytest -m live`. It makes one real AI call, confirms the `llm_calls` row,
  and prints the LangSmith run URL if configured.
