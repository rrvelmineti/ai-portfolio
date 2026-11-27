# core/guardrails: PII masking, prompt-injection defence, topic policy, approvals

Package: `ai_platform_guardrails` (in `src/`).

**Principle: fail closed.** If a guardrail errors, the AI call is blocked, never sent raw.

```python
from ai_platform_guardrails import install_guardrails, wrap_untrusted
from ai_platform_gateway import complete

install_guardrails(app, auth, session_factory, policy_path="policies/claims.yaml")

r = complete("Draft a letter to member Ana Ruiz about claim CLM-2024-0012345")
# the provider saw: "... member <PERSON_1> about claim <CLAIM_NUMBER_1>"
letter = unmask(r.text, r.mapping_id)  # same request only

r = complete(f"Summarise this file:\n{wrap_untrusted(csv_text, 'file:claims.csv')}")
if any(f.startswith("untrusted_injection") for f in r.guard_flags):
    ...  # the product decides whether to escalate
```

Scripts and workers use `configure_guardrails(session_factory)` inside a
`request_context()`.

## What happens on every AI call

```text
complete()/embed()
  └─ guard.before_*  ── topic policy (input) ── injection scan ── PII mask ── canary
        │ blocked? -> GuardrailBlocked / PolicyViolation (audited), nothing sent
        ▼
     provider call (masked text only)
        ▼
  └─ guard.after_complete ── canary leak ── exfiltration links ── topic policy (output)
                           ── new PII in output -> output_flags
```

The gateway loads this guard automatically on first use. If it can't (package missing,
spaCy model missing, Presidio error), the call fails with `GuardrailError`.

### Gateway parameters

| Parameter | Values | Meaning |
|---|---|---|
| `pii` | `"mask"` (default) | Replace PII with typed placeholders before the provider call. |
| | `"block"` | Raise `GuardrailBlocked` if any PII is found. The reasons list entity types only. |
| | `"off"` | Explicit opt-out. **Requires `pii_reason`**, e.g. `"public SEC filing"`, which is logged. |
| `injection` | `"block"` (default) | Block high-risk chat input. Borderline input is flagged, or sent to the LLM classifier if enabled. |
| | `"flag"` | Never block; add `injection:*` flags. |
| | `"off"` | No scan (used by the classifier's own call). |

Results carry counts and codes only:
- `pii_entities`, e.g. `{"PERSON": 2}`;
- `mapping_id`, for `unmask()`;
- `injection_score`;
- `guard_flags` (input side), e.g. `untrusted_injection:file:claims.csv`;
- `output_flags`, e.g. `output_pii:PHONE`.

## PII (Presidio + spaCy)

- **Model:** `GUARDRAILS_SPACY_MODEL` (default `en_core_web_lg`, pinned in `uv.lock` by
  URL). The accuracy targets are enforced on this model in CI.
- **Entities:**

  | Entity | How it's detected |
  |---|---|
  | `PERSON` | spaCy, plus names after a title or role ("Patient …", "Dear …") |
  | `EMAIL`, `IP_ADDRESS`, `US_SSN` | Presidio |
  | `PHONE` | Presidio, plus formatted US numbers and NANP-valid bare 10-digit numbers |
  | `CREDIT_CARD` | Presidio, plus a Luhn check on 12–19 digits next to card words |
  | `US_BANK_NUMBER` | 8–17 digits next to account or bank words |
  | `DATE_OF_BIRTH` | dates next to DOB words only; dates of service are left alone |
  | `ADDRESS` | USPS street suffixes, plus city, state and ZIP |
  | `MEMBER_ID` | `ABC123456789`, `M12345678`, or a weak format plus context |
  | `CLAIM_NUMBER` | `CLM-2024-0012345` |
  | `POLICY_NUMBER` | `POL-00123456` |
  | `NPI` | 10 digits passing the Luhn check with the `80840` prefix |

  `LOCATION` (city or state names alone) is **off** by default; set
  `pii.mask_location: true` to mask it.
- **`mask_text(text)`** returns `MaskResult(masked_text, entities=[type, start, end,
  score], mapping_id)`.
  - Placeholders are typed and consistent within one call or `MaskSession`, so the
    same value always becomes the same `<PERSON_1>`.
- **`unmask(text, mapping_id)`** restores the original values.
  - The mapping lives only in the request context's `vault`: never logged, traced or
    stored, and gone when the request ends.
  - Afterwards it raises `MappingExpired`.
- **`mask_records(rows, columns=None)`:** the column type comes from the header plus
  sampled values. Flagged columns are masked cell by cell with consistent placeholders,
  and other text cells are scanned as free text.
- **`privacy_report(*results)`:** counts per entity and flagged columns, never values.
- **HIPAA Safe Harbor mode** (`hipaa_safe_harbor: true`, or `safe_harbor=True` per call):
  - every date keeps only its year;
  - ZIP codes keep their first 3 digits, and the restricted low-population prefixes
    become `000`;
  - ages over 89 become `90+`;
  - DOB stays fully masked.

  It generalises rather than masks, so it isn't reversible.

### Per-tenant recognizers (`GUARDRAILS_CONFIG`)

```yaml
pii:
  mask_location: false
  hipaa_safe_harbor: false
tenants:
  "<tenant_id uuid>":
    recognizers:
      - entity: MEMBER_ID
        patterns: [{name: acme_member, regex: '\bACM-\d{7}\b', score: 0.4}]
        context: [acme, member]        # single words: Presidio matches per token
```

The tenant comes from the access token (via the request context), so another tenant's
formats never apply.

## Prompt injection: layered

| Layer | What it does |
|---|---|
| Normalisation | NFKC, zero-width removal, Cyrillic/Greek look-alikes, letter spacing, leetspeak; decodes base64, hex, `\u` escapes, URL encoding and rot13, then scans again |
| Rules | Weighted intent phrases: instruction override (EN/ES/FR/DE/PT/IT), jailbreak personas, system-prompt extraction, delimiter breaking, hidden instructions in data, exfiltration, tool abuse, skipping oversight, harvesting others' data. Score ≥ 0.7 blocks; 0.4–0.7 is borderline. |
| LLM classifier | `GUARD_LLM_CLASSIFIER=true`: borderline only, via the `fast` alias with PII masked. If it fails, the call is blocked. |
| `wrap_untrusted(text, source)` | Fences documents, files and CSV cells with random markers, neutralises fake fences, and adds a standing "data, not instructions" rule to the system prompt. Attacks inside fences are flagged and audited, not blocked. |
| Canary | A random token in every system prompt; if it appears in the output, the call is blocked and audited. |
| Output checks | Markdown or HTML links and images to domains not on the allowlist are blocked (exfiltration). `check_tool_calls(calls, allowlist)` checks tool names. |

Every block or flag writes `guardrail.blocked` or `guardrail.flagged` to the audit log.
The row includes the request id, reasons, score, and a payload hash and length, never
the payload.

## Topic policy (`GUARDRAILS_POLICY`, per product)

```yaml
product: rewards-advisor
link_allowlist: []
tool_allowlist: [lookup_offer]
blocked_topics:
  - name: investment_advice
    applies_to: both                  # input | output | both
    refusal: "I can't give investment advice..."
    phrases: [investment advice]
    patterns: ['\bshould\s+i\s+(?:buy|sell)\b.{0,40}\bstocks?\b']
```

A match raises `PolicyViolation(topic, refusal)`, which the product shows to the user,
and is audited as `guardrail.policy_blocked`. Examples are in `policies/`.

## Human approval

- `require_approval(action, payload_summary)` creates a pending row in
  `guardrails.approval_requests`. The summary is PII-masked before it's stored.
- **Who decides:** analysts and admins. Nobody may decide their own request (four-eyes),
  and rejections need a reason.
- **Expiry:** pending requests expire after `APPROVAL_TTL_HOURS` (default 168).
- **Who sees what:** viewers see their own requests; analysts their own plus pending
  ones in the tenant; admins everything in the tenant.
- **Audit:** `approval.requested`, `.approved`, `.rejected` and `.expired`.
- **API:** `/approvals` (list, get, create, `/{id}/approve`, `/{id}/reject`).

## Evaluation

`uv run python scripts/guardrails_report.py --check` scores the synthetic corpus in
`eval/` and writes `docs/guardrails-results.md`. CI fails below these targets:
- recall 100% on SSN, MEMBER_ID, CLAIM_NUMBER, NPI, CREDIT_CARD, EMAIL and PHONE;
- recall ≥ 95% on PERSON and ADDRESS;
- 100% detection of the attack set;
- under 10% false positives on the benign set.

## Configuration

| Variable | Default | |
|---|---|---|
| `GUARDRAILS_SPACY_MODEL` | `en_core_web_lg` | smaller models are faster but untested against the targets |
| `GUARDRAILS_CONFIG` | none | PII settings and tenant recognizers |
| `GUARDRAILS_POLICY` | none | product topic policy and allowlists |
| `GUARD_LLM_CLASSIFIER` | `false` | LLM second opinion on borderline inputs |
| `APPROVAL_TTL_HOURS` | `168` | |
