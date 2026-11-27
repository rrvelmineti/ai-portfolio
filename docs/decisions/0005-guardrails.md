# 0005. Guardrails: Presidio PII masking, layered injection defence, fail closed

- Status: Accepted
- Date: 2026-11-27

## Context

Products send claims files, member letters and free text to third-party models. Three
risks follow:
1. Personal data leaves for a provider or a trace store.
2. Text inside a file or message hijacks the model (prompt injection).
3. The model does something risky with no human in the loop.

The data is synthetic here, but the platform must behave as if it were real. Claims work
also brings HIPAA's Safe Harbor identifiers, which go beyond dates of birth.

## Decision

1. **Fail closed, enforced in the gateway.** Every `complete()`/`embed()` passes through
   a guard. If the guard can't load or errors, the call is blocked.
   - The only bypass is `pii="off"` with a written reason, which is logged.
   - The gateway's own unit tests use a passthrough guard; the real guard is tested in
     the guardrails and integration suites.
2. **Microsoft Presidio** (analyzer + spaCy `en_core_web_lg`) for PII. We add custom
   recognizers for claims IDs, NPI (Luhn), DOB-in-context, street addresses, US phone
   formats, Luhn card numbers and bank accounts, plus per-tenant YAML patterns.
   - **Masking, not deletion:** typed placeholders keep the text useful to the model, and
     a request-scoped reversible mapping lets products show real values back to the user.
   - **HIPAA Safe Harbor** is a switch (dates to year, ZIP to 3 digits, ages over 89
     to 90+).
3. **Layered injection defence**, because no single filter is enough:
   - normalisation and decoding, then weighted rules;
   - an optional LLM classifier for borderline cases;
   - fencing of untrusted content with a standing instruction;
   - a canary token;
   - output checks for exfiltration links and unlisted tools;
   - audit rows for every block or flag.
4. **Chat versus documents.** Attacks in chat messages are blocked. Suspicious content
   inside `wrap_untrusted` fences is flagged, logged and kept fenced, so one bad CSV cell
   doesn't stop a whole file from onboarding; products decide whether to escalate.
5. **Topic policy and human approval** are configuration and data:
   - per-product YAML for blocked topics, with refusal messages;
   - an `approval_requests` table with four-eyes, expiry and audit.
6. **Measured, not assumed.** A synthetic corpus, committed before first scoring, sets CI
   targets. We report real numbers, including the first run, and don't tune the corpus.

## Why Presidio

- **Open source and self-hosted.** No PII is sent to a third party in order to detect PII.
  That rules out "send it to an LLM to find the PII".
- **Extensible:** pattern recognizers, validators (Luhn), context words, and per-call
  ad-hoc recognizers for tenant formats.
- **Mature NER via spaCy**, with predictable latency (about 2 s model load, tens of
  milliseconds per call).

**Alternatives considered:**
- **Cloud DLP services** (Amazon Comprehend PII, Google DLP): strong, but the text leaves
  our boundary and every call costs money.
- **Regex only:** misses names and addresses.
- **LLM-based detection:** circular (it sends the data out) and non-deterministic.

## Why layered injection defence

Prompt injection has no complete solution. Rules miss paraphrases, classifiers can be
fooled, and models follow instructions by design. Layers make an attack hard to land and
contain the damage when one gets through:
- The **canary** catches system-prompt extraction even when the request looked innocent.
- **Output link blocking** stops the common exfiltration path (markdown images), whatever
  the input looked like.
- **Fencing** lowers the chance the model treats data as instructions.
- **The approval gate** means risky actions need a human, even if the model is fooled.
- **Audit rows** make attempts visible and countable.

## Known limits

- **No detector catches everything.** 100% on our 35 attacks says nothing about attacks we
  haven't imagined; the rules are heuristics, and new phrasings need new rules or the
  classifier.
- **PII recall isn't 100% for names.** Common-word names ("Will Stone") are sometimes
  missed. Placeholders reduce exposure; they don't make it zero.
- **NANP phones:** bare 10-digit numbers matching the US numbering plan are masked as
  phones, so some unrelated numbers are masked too (safe direction).
- **English only:** the NER model is English. Rules cover core override phrases in five
  more languages, but not their PII.
- **Images aren't scanned:** PII or instructions inside images pass through untouched.
- **Output PII flags are advisory:** they flag, they don't block, because models may
  legitimately produce public names.
- **Performance:** about 800 MB of RAM for the model; masking adds tens of milliseconds
  per call.
- **Audit trust:** the audit log protects against the application, not a database owner
  (see ADR 0003).

## Consequences

- Every product gets masking, injection defence and the approval gate by calling
  `install_guardrails`.
- CI installs the 400 MB spaCy model (downloaded each run, about 4 s) and enforces the accuracy
  targets.
- `pii="off"` exists for public documents (e.g. 10-K filings in Query Desk), always with a
  logged reason.
- Phase 0.4's `set_content_redactor` hook now always masks LangSmith content.
