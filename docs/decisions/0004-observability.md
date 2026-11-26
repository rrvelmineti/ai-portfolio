# 0004. Observability: Postgres llm_calls + LangSmith + Prometheus, content off by default

- Status: Accepted
- Date: 2026-11-26

## Context

Every request and every AI call must be traceable end to end, with cost and latency,
for three audiences:
- engineers debugging a request;
- operators watching error rates and latency;
- the case studies, which need "cost per answer / per file".

Secrets must never leak, and prompts often contain customer text (synthetic here, but
treated as real), so prompt text must stay out by default. Local dev and CI must need
no accounts.

## Decision

Three stores, each doing what it's good at. All three are fed from one gateway hook
and one request context.

1. **`observability.llm_calls` in Postgres:** one row per AI call, with request id,
   tenant, user, product, alias, provider, model, attempts, fallback, tokens, cost,
   latency and status. **It never holds text.**
   - It's our own durable record: queryable with SQL, joinable with tenants and the
     audit log, and the source for `scripts/usage_report.py`.
   - It uses the database we already run.
2. **LangSmith (optional):** run trees for debugging prompts and workflows.
   `traced()` groups the steps of one workflow.
   - Metadata always goes.
   - Prompt and response text only with `LANGSMITH_LOG_CONTENT=true`, and only through
     `redact_content()`, which is where Presidio plugs in next phase.
3. **Prometheus metrics (`/metrics`):** low-cardinality counters and histograms for
   dashboards and alerts (route templates, provider/model/status). Health and
   readiness endpoints sit alongside.

**The common layer:**
- One `RequestContext` (in `ai_platform_common`, a ContextVar), so logging, auth, the
  gateway and the audit log all agree on `request_id`, tenant and user.
- One JSON log format with key-based redaction.
- Optional Sentry for errors, with no-PII `data_collection` and the same redaction.

## Why not OpenTelemetry (yet)

OpenTelemetry is the standard for distributed tracing. We deliberately start without
it:
- **One service, in-process calls.** There's no distributed trace to stitch together
  yet. The request id gives us end-to-end correlation across logs, audit, `llm_calls`
  and LangSmith.
- **AI-specific views.** Run trees, token usage and prompt playback are LangSmith's
  strength. With OpenTelemetry we would need a GenAI semantic-convention exporter and a
  backend that renders it well.
- **Zero infrastructure.** No collector, exporter endpoint or backend account; CI
  stays account-free.
- **Cost reporting wants SQL, not spans.** `llm_calls` answers "cost per answer per
  product per day" with one query.

**Trade-offs we accept:**
- No spans for DB queries or outgoing HTTP, and no automatic propagation of
  `traceparent` across services.
- LangSmith is a vendor. It's optional, and only metadata leaves by default.
- Two places to look (Prometheus for aggregates, LangSmith for individual traces),
  joined by `request_id`.
- The `llm_calls` insert is synchronous in the call path: a few milliseconds. It can
  move to a queue if it ever matters.
- The in-process Prometheus registry is per worker. Multi-process deployments need
  `prometheus_client` multiprocess mode or a push gateway (infrastructure phase).

## How to add OpenTelemetry later

The seams are already in place:
1. **Requests:** add `opentelemetry-instrumentation-fastapi` (or an OpenTelemetry
   middleware next to `ObservabilityMiddleware`). Set the trace id as `request_id`, or
   store it in `RequestContext`, so logs and `llm_calls` keep correlating.
2. **AI calls:** register another `on_call_complete` hook that emits a GenAI span using
   the `gen_ai.*` semantic conventions from the same `CallRecord`. Content stays behind
   `capture_content()` and `redact_content()`.
3. **Infrastructure:** SQLAlchemy and HTTP-client instrumentation and an OTLP exporter
   via env vars, kept a no-op when unset, like LangSmith and Sentry today.
4. **LangSmith:** it accepts OpenTelemetry ingestion, so the `LangSmithTracer` could
   later be replaced by an OTLP exporter pointed at LangSmith.

`llm_calls` and the usage report stay as they are; they're the record, not the
telemetry pipe.

## Consequences

- Products get everything with `install_observability(app, session_factory, product=...)`.
  Scripts and workers use `setup_tracing()` plus `request_context()`.
- Prompt text never reaches logs, the database or Sentry. It reaches LangSmith only
  when explicitly enabled, and always after the redaction hook.
- Auth now uses the shared request context. Its own request-id middleware and
  `RequestInfo` are gone.
- CI needs no new services. The LangSmith client and Sentry are mocked in tests.
