# products/claims-onboarding (Spec 1)

Takes each new health plan client's claims extract from first file to go-live:
- AI column mapping that sees headings only;
- privacy masking before any model call;
- versioned rules checked by code on every row;
- issue letters and resubmissions;
- a four-eyes go-live sign-off.

It rebuilds [`claims-onboarding-platform`](https://github.com/rrvelmineti/claims-onboarding-platform)
on the shared platform (auth, gateway, guardrails, observability). What the old app has,
and what we keep or replace: [`docs/current-state.md`](docs/current-state.md).

**Synthetic data only.** This is not a HIPAA-compliant system.

## Phases

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
   - ADR 0009; [current state of the old app](docs/current-state.md).
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

## Phase 1: what exists now

- **Data model:** schema `claims`, migration `0001`; design in
  [ADR 0009](../../docs/decisions/0009-claims-data-model.md).
- **Service** (`claims_onboarding.service`): tenant-scoped, guarded, audited operations.
- **API** (`claims_onboarding.app:create_app`):

| Method and path | Who | What |
|---|---|---|
| `POST /clients`, `GET /clients`, `GET /clients/{id}` | analyst writes, viewer reads | Clients (health plans) in your tenant |
| `POST /clients/{id}/files` | analyst | Register a file: metadata and headings, optional `previous_file_id` |
| `GET /files/{id}` | viewer | File with its columns and mappings |
| `POST /files/{id}/mappings` | analyst | Record a mapping suggestion |
| `POST /mappings/{id}/decision` | analyst | Approve or reject a mapping |
| `POST /files/{id}/transitions` | analyst | Move status `{"to": ..., "reason": ...}` |
| `POST /files/{id}/signoff` | manager (admin) | Go-live decision; four-eyes enforced. Rejecting needs `reason` and `return_to`: `under_review` or `issues_sent` |

Plus `/auth/*`, `/approvals`, `/metrics`, `/healthz` and `/readyz` from the platform.

Run it locally:

```bash
set -a; source .env; set +a
export RUN_MIGRATIONS_ON_START=true     # local only
uv run uvicorn --factory claims_onboarding.app:create_app --port 8001 --log-level warning
```

Then open http://127.0.0.1:8001/docs.
