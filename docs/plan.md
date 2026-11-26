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

## Claims Onboarding

AI-assisted claims intake: guided submission, structured field extraction from
free text and documents, PII redaction. Synthetic claims data only.

- Status: **Not started**

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
