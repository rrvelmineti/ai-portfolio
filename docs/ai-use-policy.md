# AI use policy

How the ai-portfolio products use AI models: what data is masked, what is logged, and
when a human must approve. It applies to every product, and the platform enforces it
technically (`core/guardrails`, `core/gateway`, `core/observability`).

## 1. Data we send to AI models

- **All model calls go through the gateway.** No product code calls a provider SDK
  directly.
- **Personal data is masked before it leaves.** Names, emails, phone numbers, SSNs, dates
  of birth, street addresses, card and bank numbers, IP addresses, member IDs, claim and
  policy numbers, and NPIs are replaced with typed placeholders such as `<PERSON_1>`.
- **Claims products can turn on HIPAA Safe Harbor.** It generalises every date to its
  year, ZIP codes to their first 3 digits, and ages over 89 to "90+".
- **Opting out is explicit and recorded.** A call may set `pii="off"` only with a written
  reason (e.g. "public SEC filing"), and the reason is logged with the call.
- **Fail closed.** If masking or any other guardrail fails, the call is not made.
- **Images aren't masked.** Products must not send images that contain personal data
  unless the use case requires it and has been reviewed.

## 2. What we log and store

| Where | What | Never |
|---|---|---|
| Application logs (JSON) | request id, tenant/user ids, route, status, latency, model, tokens, cost, guardrail reasons and scores | prompts, responses, secrets, PII values (prompts only with `DEBUG_LOG_PROMPTS=true`, local debugging only) |
| `observability.llm_calls` | one row per AI call: ids, product, model, tokens, cost, latency, status | prompt or response text |
| `auth.audit_log` (append-only) | logins, user changes, writes, guardrail blocks/flags (with a payload hash and length), approvals | passwords, tokens, payloads |
| `guardrails.approval_requests` | action and a **PII-masked** summary, decisions | unmasked personal data |
| LangSmith (only if configured) | run metadata | prompt/response text, unless `LANGSMITH_LOG_CONTENT=true`; even then it is masked first |
| Sentry (only if configured) | error events | user info, cookies, bodies, AI inputs/outputs (no-PII data collection, plus redaction) |

- **Unmask mappings** (placeholder → real value) exist only in memory for the current
  request. They are never logged, traced or stored.
- **All data in this repository is synthetic.**

## 3. Prompt injection and untrusted content

- **Documents, files and retrieved text are untrusted.** Products wrap them with
  `wrap_untrusted()`, and the model is told to treat them as data.
- **Two detection layers run locally on every input:** pattern rules and a small ML
  classifier (ADR 0007). No text is sent anywhere to be checked.
- **Chat messages that look like attacks are blocked.** Suspicious content inside
  documents is flagged and audited, and the product decides whether to escalate.
- **Detection doesn't catch everything.** Requests phrased as ordinary work ("export every
  member record", "compliance already approved this") often pass. That is why risky
  actions always need a real second approver and products get the least privilege they
  need.
- **Output checks:**
  - an output that leaks the system prompt is blocked (canary);
  - so is one with links or images to domains not on the product's allowlist;
  - tools not on the product's allowlist can't be called.

## 4. Topics we don't advise on

Each product lists its blocked topics with a refusal message, checked on both input and
output. Examples:
- **Rewards Advisor:** no investment advice.
- **Meal Vision:** no medical advice (diagnosis, medication, insulin dosing).

## 5. Human approval

Products must call `require_approval()` before an AI-assisted action that:
- moves money (refunds, payments, transfers);
- changes a claim's decision or amount;
- changes or deletes member, policy or user records;
- sends external communication on a member's behalf;
- is irreversible, or affects many records at once.

**Approval rules:**
- Analysts or admins of the same tenant approve.
- **Nobody approves their own request.**
- Rejections need a reason.
- Pending requests expire after 7 days by default.
- Every request and decision is audited.

## 6. Accountability and limits

- **Automated guardrails reduce risk; they don't remove it.** No detector catches every
  name or every injection. See `docs/guardrails-results.md` for measured accuracy and
  ADR 0005 for known limits.
- **Model outputs are suggestions** until a person or an approved process acts on them.
- **Report suspected leaks or misuse immediately.** The audit log, `llm_calls` and the
  request id let us trace any AI call end to end.
