# Claims onboarding: current state of the existing app

The source is [`rrvelmineti/claims-onboarding-platform`](https://github.com/rrvelmineti/claims-onboarding-platform),
last updated 2026-11-19 and deployed on Render's free tier as `claims-onboarding-platform`.
This note records what it does, what Spec 1 reuses and what it replaces. **No code has
been copied yet.** Each piece is ported in the Spec 1 phase that needs it (see
[`docs/plan.md`](../../../docs/plan.md)).

## What the existing app is

There are two generations in one repository:

- **`claims_onboarding/`:** the first version, a Streamlit page. Rules from a text file,
  mapping suggestions, a readiness report and a client issue letter.
- **`claims_platform/` + `claims_platform_web/`:** "pilot v1.0", about 3,300 lines of
  Python plus a React/TypeScript UI.
  - A multi-client FastAPI service on SQLite or PostgreSQL 16, with its own users and
    sessions, roles and per-client access.
  - Versioned mappings and rule sets with approval, validation runs, exceptions tracked
    across resubmissions.
  - A hash-chained audit trail, communications, go-live approval, and export to a
    simulated downstream system.
  - 49 scenario checks (clients A–F) pass on SQLite and PostgreSQL.

The repository also contains an unrelated "orders" CSV assistant and Streamlit pages.

## Capabilities

### AI column mapping

- **Where:** `claims_platform/mapping_service.py`, `claims_onboarding/mapping.py`.
- **How:**
  - name heuristics (abbreviations, field words, per-heading scores), with an optional
    Gemini call;
  - **the model sees headings only, never cell values**;
  - model output is checked against the real headers and target fields before use.
- **Uncertainty handling:**
  - abstains below confidence 0.5;
  - flags two candidates within 0.15 of each other as ambiguous, which the analyst must
    confirm;
  - saved templates reuse a previous mapping for identical headers.
- **Each correction is logged:** suggested vs chosen.
- **Result on the 2,000-row synthetic sample:** 9 of 12 fields mapped correctly, 0 wrong,
  3 left for the analyst.

### Rules engine and the 52 planted errors

- **Where:** `claims_onboarding/rules.txt` + `rules.py` (about 260 lines).
- **Rule format:** plain language, one target field per line:
  - required, unique, number, whole number, not negative;
  - exactly N digits or characters;
  - six date formats; not in the future; not before another field; not greater than
    another field;
  - ICD-10 format; one of A / B / C;
  - a `(warning)` suffix makes a rule report without blocking. Everything else is a
    blocker.
- **The 12 target fields:** `claim_id`, `member_id`, `member_dob`, `provider_npi`,
  `service_date`, `diagnosis_code`, `procedure_code`, `place_of_service`,
  `billed_amount`, `allowed_amount`, `paid_amount`, `claim_status`.
- **Every row is checked by code; no model decides.** Findings are capped at 5,000.
- **Answer key:** `examples/claims/client_claims.csv` (2,000 rows) with
  `examples/claims/answer_key.json`, which holds **52 planted errors** across 14
  field/rule pairs (e.g. NPI not 10 digits ×6, duplicate claim_id ×5, allowed >
  billed ×5). All 52 are found with no false alarms.
- **Speed:** a 100,000-row file validates in about 5 seconds.

### Approval step

- Mappings and rule sets go through draft, then pending approval, then approved or
  rejected (approving supersedes the previous version).
- **Nobody approves their own work:** the author can't be the approver.
- **Go-live decision** (`workflow.decide_go_live`): readiness is computed by code
  (`readiness.py`: required fields mapped, full file scanned, zero blockers), then a
  person approves or rejects.

### Issue letters

- **Where:** `claims_onboarding/handoff.py`, `claims_platform/comms.py`.
- **Content:** findings grouped by rule, **in the client's own column names**, with up to
  5 example lines per rule and a "how to fix" sentence for each rule type.
- **Optional AI wording** is checked against the facts: every number in the AI text must
  be one of the allowed numbers.
- Letters are drafted, then sent, and recorded as communications.

### Resubmission comparison

- Exceptions are tracked per client across submissions
  (`processing.sync_exceptions`, `reporting.resubmission_history`).
- Each new submission is compared with the previous one: **fixed**, **still open
  (unresolved)**, **new** and **regressed**.
- Exceptions that no longer appear are resolved automatically. Scenario D tests this.

### Other pieces

- Fernet-encrypted file storage (on disk or in PostgreSQL, because Render's free disk is
  wiped on restart).
- Background validation runs with interrupted-run recovery; exports to a simulated
  downstream system that never loads a claim twice.
- Milestones and KPIs; the React UI (dashboard, mapping workspace, exceptions,
  resubmission tracker, go-live).
- Business docs: BRD, traceability, process maps, UAT, runbooks.

## What Spec 1 reuses

| Item | Where it lands |
|---|---|
| Mapping heuristics, the 0.5 abstain and 0.15 ambiguity thresholds, the headings-only rule, templates | Phase 5 (RAG mapping), as the baseline to beat |
| Rule format and engine; `rules.txt` as the built-in rule library | Phase 6 (rules), as `rule_definitions` with `kind = built_in` |
| The 2,000-row sample and its 52-error answer key | Phase 6 regression test; Phase 2 extends it to 10 client files |
| Readiness logic (code decides, never a model) | Phase 1 guard: `ready_for_signoff` only with zero blocker results |
| Issue letter structure and the AI fact check | Phase 7 (integrations) |
| Resubmission comparison (fixed / open / new / regressed) | Phase 8 (recurring feeds), across files linked by `previous_file_id` |
| Four-eyes approval | Phase 1: whoever approved a file's mappings can't sign it off |
| Business docs (BRD, process maps, UAT) | Phase 9 (showcase), rewritten for the new platform |

## What Spec 1 replaces

| Old | New | Why |
|---|---|---|
| Own users, sessions, roles, `client_access` | Platform auth: tenants, JWT, admin / analyst / viewer | One login system across the products. A tenant is the onboarding organisation; a client is a health plan inside it (ADR 0009). |
| Integer IDs, no tenant column | UUIDs, `tenant_id` on every table, composite `(tenant_id, id)` foreign keys | Tenant isolation enforced by the database, not only the code |
| One JSON `mappings` row per version | One `mappings` row per column with suggested / approved / rejected, confidence, model and decider | Per-column review and audit |
| Rule set as a text blob per client | Versioned `rule_definitions` rows, built-in or analyst-built | No-code rule builder (Phase 6) |
| Direct Gemini HTTP calls | The platform gateway with guardrails: PII masking, injection checks, cost and usage tracking | One controlled path to every model |
| Own hash-chained audit log | The platform's append-only audit log | One audit trail. **Gap, see below.** |
| Fernet file storage | Decided in Phase 3 (ingestion) | Upload, SFTP and S3-compatible sources |
| Streamlit pages, the orders assistant | Not carried over | Out of scope for Spec 1 |
| React UI | Decided in Phase 9 | Phase 1 ships an API only |

## Known gaps after moving to the platform

- **No hash chain on the platform audit log.**
  - The old app's chain was a SHA-256 over the previous hash plus each entry, with a
    `verify()` that detects edits and deletions, including entries removed from the end.
  - The platform's log is append-only (a trigger blocks UPDATE, DELETE and TRUNCATE
    except the owner-only 7-day retention), but it has no tamper-evidence beyond that.
  - Tracked as a platform backlog issue:
    [rrvelmineti/ai-portfolio#15](https://github.com/rrvelmineti/ai-portfolio/issues/15).
- **No per-client analyst assignment ("minimum necessary").** Every analyst in a tenant
  sees all its clients. Recorded as future work in ADR 0009.
- **The old README's "Deploy to Render" button points at `rrvelmineti/ai-pipeline-assistant`,**
  not this repository:
  [rrvelmineti/claims-onboarding-platform#14](https://github.com/rrvelmineti/claims-onboarding-platform/issues/14).
