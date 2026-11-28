# 0009. Claims onboarding: multi-tenant data model, file status machine and four-eyes

- Status: Accepted
- Date: 2026-11-28

## Context

Spec 1 rebuilds the claims onboarding app
([`claims-onboarding-platform`](https://github.com/rrvelmineti/claims-onboarding-platform))
on the shared platform. What it has and what we keep is in
[`products/claims-onboarding/docs/current-state.md`](../../products/claims-onboarding/docs/current-state.md).

The old app had its own users and per-client access lists, integer IDs and no tenant
column. Phase 1 puts the data model on the platform's tenants, roles and audit log
before any AI, masking or rules work lands on it.

## Decision

### Tenants and clients

- A **tenant** is the onboarding organisation using the platform: its users, roles and
  audit log come from `ai_platform_auth`.
- A **client** is a health plan being onboarded inside a tenant.
- All of a tenant's analysts see all of its clients, so there's no per-client access table
  in Phase 1.

### Schema `claims` (own Alembic environment, version table `alembic_version_claims`)

| Table | Notes |
|---|---|
| `clients` | `code` is unique per tenant; status onboarding / live / on_hold |
| `data_dictionaries` | Versioned target fields (JSONB). `client_id` null = the tenant default. |
| `files` | Metadata only in Phase 1. Status lifecycle (below). `sequence` per client. `previous_file_id` links a resubmission. |
| `file_columns` | Headings as the client wrote them |
| `mappings` | One row per (column, target field): suggested / approved / rejected, confidence, model (null = a person suggested it), `suggested_by`, `approved_by`, `rejected_by`, `decided_at` |
| `rule_definitions` | Versioned by `rule_key`. `kind` built_in / analyst. Blocker or warning. A new version retires the active one. |
| `rule_results` | Failed rows per rule per file. Severity is copied from the rule. Examples are stored masked. |
| `issue_letters` | Draft / sent, body stored masked |
| `go_live_signoffs` | Approved, or rejected with a reason and `returned_to` (`under_review` or `issues_sent`); at most one approval per file. A CHECK enforces the outcome. |

- Statuses are **strings with CHECK constraints**, not Postgres enums, so later phases
  extend them with a constraint change instead of an enum migration.
- A CHECK on `mappings` ties the decision to its decider: approved means `approved_by` is
  set and `rejected_by` is not, and so on.

### Tenant isolation, twice

1. **In code:** every service function takes the `TenantScope` from the access token, and
   every query filters on it. Another tenant's IDs raise `NotFound` (HTTP 404, never
   403), so their existence never leaks.
2. **In the database:** every parent has `UNIQUE (tenant_id, id)`, and every child
   references it with a **composite foreign key** `(tenant_id, parent_id)`.
   - So a row can't point at another tenant's row even if code bypasses the service.
   - Two keys go further:
     - a mapping's column must belong to the mapping's own file:
       `(tenant_id, file_id, file_column_id)`;
     - a resubmission must follow a file of the same client:
       `(tenant_id, client_id, previous_file_id)`.
   - Tests insert such rows directly and expect the database to refuse them.

### File status machine

```
received -> masked -> mapped -> under_review -> rules_run -> issues_sent <-----------+
                        ^          |   ^                  \-> ready_for_signoff -> live
                        +send back-+   +----- rejected sign-off (internal fix) ----+ |
                                                rejected sign-off (client resubmits)-+
```

- Any other change raises `InvalidTransition` (HTTP 409).
- **Send back** (`under_review → mapped`) requires a reason, stored in the audit row.
- **`issues_sent` and `live` are final.** The client's corrected file is a new file with
  `previous_file_id` set, starting at `received`. Only a file at `issues_sent` can be
  "previous".
- **Every move out of `ready_for_signoff` is a go-live sign-off,** never a plain
  transition:
  - **approved** → `live`;
  - **rejected** → a **reason is required**, plus `return_to`:
    - `under_review` for an internal fix: the analysts correct mappings, re-run the
      rules (a re-run replaces that rule's earlier result) and ask for sign-off again;
    - `issues_sent` when the client must send a corrected file, which comes in as a
      new file with `previous_file_id`.
  - **Both are audited:** `claims.signoff.recorded {decision, reason, return_to}` and
    `claims.file.status_changed {from: ready_for_signoff, to, reason}`.
  - Four-eyes applies to rejections too.
- **Guards** (data checks, HTTP 409):
  - entering `rules_run` needs no undecided mappings and at least one approved one;
  - `rules_run → ready_for_signoff` needs recorded rule results **with zero failed
    blocker rules**. Otherwise the only way on is `rules_run → issues_sent`.
- **`masked` is a status-only placeholder** until Phase 4 (privacy). A strict-xfail test
  says Phase 4 must refuse to leave `masked` without a privacy report.

### Roles and four-eyes

| Platform role | In claims onboarding |
|---|---|
| viewer | read only |
| analyst | create clients, register files, suggest and decide mappings, move statuses up to `ready_for_signoff` |
| admin (= manager) | everything an analyst can do, and **the go-live sign-off** |

- **Four-eyes:** anyone who approved any mapping on a file can't sign off that file's
  go-live (HTTP 403). This includes managers, who may also approve mappings because
  admin ranks above analyst.
- The rule is checked in the service, not only the HTTP layer, so it holds for every
  caller.

### Audit

- Every change writes a `claims.*` audit row in the **same transaction**:
  - `client.created`, `file.registered`, `file.status_changed {from, to, reason}`;
  - `mapping.suggested`, `mapping.decided`;
  - `rule.defined`, `rule_result.recorded`;
  - `signoff.recorded {decision, reason, return_to}`, `client.live`.
- If the change rolls back, so does its row, and refused changes write nothing.
- The platform's generic `http.post` row is skipped for these requests because they are
  already audited.

## Consequences

- **Isolation holds even with a bug in the code,** because the foreign keys enforce it.
  The cost is a few extra unique constraints and wider foreign keys.
- **Resubmissions form a chain of files** rather than a loop in one file's status. Phase 8
  (recurring feeds) builds the comparison on that chain.
- **Phase 1 stores no file contents and calls no models.** `rule_results` and
  `issue_letters` exist so the guards and later phases have somewhere to write; their
  producers arrive in Phases 6 and 7.
- **No audit hash chain:** the old app's tamper-evident chain is not on the platform log
  yet ([rrvelmineti/ai-portfolio#15](https://github.com/rrvelmineti/ai-portfolio/issues/15)).
- **Not deployed yet:** the hosted demo's app-role grants (`ai_platform_demo.roles`)
  don't cover the `claims` schema. They're added when the claims app is deployed.

## Future work

- **Per-client analyst assignments (HIPAA "minimum necessary").** Today any analyst in a
  tenant can see every client. Real use needs an assignment table (user × client, with a
  role), so analysts see and act only on their assigned clients.
  - It would be enforced in the same two places as tenant isolation: queries filtered by
    assignment, plus 404 for unassigned clients.
  - Manager sign-off still requires four-eyes.
