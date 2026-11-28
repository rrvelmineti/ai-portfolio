# Plan

Build order: shared platform first, then products one at a time. Each phase is
planned, approved, built on its own branch, and recorded in `CHANGELOG.md`.

Status values: **Not started** · **In progress** · **Done**

## Platform

Shared foundation used by every product: gateway, auth, observability (JSON
logging, tracing, LangSmith), guardrails (Presidio PII redaction, LLM safety),
common utilities, CI and infrastructure.

- Status: **In progress**
- Phases:
  - 0.1 Foundation (repo structure, tooling, CI) — **Done**
  - 0.2 Model gateway (`ai_platform_gateway`: providers, routing, fallback,
    retries, cost, logging, LangChain adapter) — **Done**
  - 0.3 Auth (`ai_platform_auth`: tenants, users, roles, JWT + rotating refresh
    tokens, lockout, rate limiting, append-only audit log; `platform/` renamed to
    `core/`) — **Done**
  - 0.4 Observability (`ai_platform_observability`: request context, JSON logs with
    redaction, access log, `llm_calls` table, LangSmith, Prometheus metrics, health,
    Sentry, usage report) — **Done**
  - 0.5 Guardrails (`ai_platform_guardrails`: Presidio PII masking with Safe Harbor
    mode, layered prompt-injection defence, topic policy, human approval gate,
    evaluation corpus with CI targets) — **Done**
    - Follow-up: held-out and public guardrails evaluation, report only (ADR 0006)
      — **Done**
    - Follow-up: guardrails v2 (local ML injection classifier, per-layer thresholds,
      PII leak fixes, held-out v2; ADR 0007) — **Done**
  - 0.6 Free deployment (ADR 0008): container image, compose demo profile,
    least-privilege database roles, guest logins, AI usage caps, nightly reset, CI image
    build + Trivy, Terraform AWS path validated in CI, gated deploy workflow
    — **In progress** (everything except the hosted deploy is built; the host choice is
    open because the Leapcell spike is paused)

## Claims Onboarding (Spec 1)

Takes each new health plan client's claims extract from first file to go-live: AI
column mapping (headings only), privacy masking, versioned rules checked by code,
issue letters and resubmissions, and a four-eyes go-live sign-off. Rebuilds
[`claims-onboarding-platform`](https://github.com/rrvelmineti/claims-onboarding-platform)
on the shared platform. Synthetic claims data only.

- Status: **In progress**
- Phases:

  1. **Multi-tenant data model** — **In progress** (this phase)
     - Tables in schema `claims` (Alembic, `tenant_id` on everything, composite
       `(tenant_id, id)` foreign keys): clients, data_dictionaries (versioned), files
       (status lifecycle), file_columns, mappings (suggested / approved / rejected,
       confidence, model, approver), rule_definitions (versioned, built-in vs
       analyst-built), rule_results, issue_letters, go_live_signoffs.
     - File status machine: received → masked → mapped → under_review → rules_run →
       issues_sent | ready_for_signoff → live; send-back under_review → mapped with a
       required reason; invalid transitions raise. ready_for_signoff only with zero
       blocker rule results. A rejected sign-off needs a reason and goes back to
       under_review (internal fix) or issues_sent (client resubmits). A corrected file
       is a new file with `previous_file_id`.
     - Roles: analyst maps and reviews; manager (admin) signs off; viewer read-only;
       four-eyes: whoever approved a file's mappings can't sign off its go-live.
     - Every state change writes to the audit log.
     - FastAPI app mounting auth, observability and guardrails; minimal endpoints to
       create a client, register a file, decide mappings, move status and sign off.
     - ADR 0009; [current state of the old app](../products/claims-onboarding/docs/current-state.md).
  2. **Synthetic data:** 10 client claims files with different headers and formats and
     planted errors, each with an answer key — Not started
  3. **Ingestion:** upload, SFTP drop and S3-compatible watch folder; duplicate and format
     checks — Not started
  4. **Privacy:** Presidio masking before any model call, `hipaa_safe_harbor` profile,
     privacy report per file — Not started
  5. **RAG mapping:** data dictionaries + approved mappings in pgvector; compare Gemini vs
     Groq (free tiers only) on accuracy, cost and speed — Not started
  6. **Rules:** built-in claims rule library (ported from the old engine and its 52-error
     answer key) + no-code rule builder with AI-suggested rules — Not started
  7. **Integrations:** issue letters, Slack webhook, HubSpot free CRM sync, an automation
     flow via free n8n / GitHub Actions, and a chat assistant ("what's blocking client
     X?") on our own gateway. Power Automate / Copilot Studio / Azure OpenAI only if a
     free developer tenant is available; otherwise documented as the enterprise path
     — Not started
  8. **Recurring feeds:** reapply approved mappings and rules to the next file, flag only
     changes — Not started
  9. **Showcase:** metrics dashboard, implementation kit, demo video, case study — Not started

## Query Desk

Retrieval-augmented Q&A over a document knowledge base (Postgres + pgvector,
LangChain) with cited answers. Synthetic documents only.

- Status: **Not started**

## Rewards Advisor

Recommends rewards/loyalty options from synthetic spending patterns and
programme rules, with explanations.

- Status: **Not started**

## Meal Vision

Image-based meal analysis: identify food items and estimate nutrition.
Synthetic or openly licensed images only.

- Status: **Not started**
