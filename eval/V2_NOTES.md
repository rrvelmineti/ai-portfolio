## Guardrails v2: before vs after (plain English)

**How these numbers were produced.** Held-out v2 and the deepset test split were scored
**once**, in the last step of the guardrails-v2 branch, after every detector change was
committed. In the same step the **guardrails v1 code** (`main` at `45e72a3`) was run on the
same v2 and test data, so "before" and "after" use identical data. Nothing was changed after
that run.

| | Guardrails v1 (before) | Guardrails v2 (after) |
|---|---:|---:|
| **Injection: v2 attacks caught (unseen)** | 5/40 (12.5%) | **29/40 (72.5%)** |
| Injection: v2 benign wrongly flagged | 2/40 (5.0%) | 3/40 (7.5%) |
| **Injection: deepset test attacks caught (public)** | 6/60 (10.0%) | **24/60 (40.0%)** ¹ |
| Injection: deepset test benign wrongly flagged | 0/56 | 0/56 |
| Injection: dev attacks / benign flagged (CI gate) | 35/35 / 0/35 | 35/35 / 0/35 |
| Injection: v1 attacks caught (tuning data, optimistic) | 17/40 | 35/40 |
| **PII: v2 values found with the right type (unseen)** | 38/65 (58%) | **50/65 (77%)** ² |
| PII: v2 values fully leaked (any type counts) | not measured in v1 | 14/65 (22%), plus 2 partial |
| PII: dev targets (CI gate) | all met | all met, 0 partial leaks |

¹ guard-small was trained on the deepset **train** split. The vendor says the test split
was excluded, but the two share a distribution, so this is not as independent as it looks.

² **Contamination, disclosed:** while developing the PII fixes, 8 of the 60 v2 lines
(`claude_v2-pii-007, 010, 014, 015, 019, 021, 022, 034`) were typed from memory as test
inputs. The author of v2 also wrote the fixes. No code was changed in response to those
inputs, but those lines are not cleanly unseen. **Without them, v2 PII is 42/56 (75%) found
and 13/56 (23%) fully leaked.** Those 8 lines hold 9 values, of which 8 were detected (the
miss was a name).

### What got better

- **Injection on unseen text went from catching 1 attack in 8 to about 3 in 4.** The local
  classifier catches paraphrased overrides, role-play setups, hidden instructions in
  documents and attacks in German, French, Spanish and Hindi that the rules never matched.
  On v2, all 7 instruction overrides (every one in German, French, Spanish or Hindi), all
  4 system-prompt extraction requests and all 4 hidden instructions in documents were
  caught.
- **The public set** went from 6/60 to 24/60. Most remaining public misses are task
  hijacks ("forget that, now write about…"), which are handled by architecture (ADR 0007).
- **False alarms stayed low:** 0/56 on the public benign set and 0/35 on the dev set.
  v2 benign rose by one, to 3/40.
- **PII:** obfuscated emails, rural/PO Box/military addresses, written-out dates of birth
  and IDs with separators are now masked. Every dev value is fully masked except 5 names.

### Known limits

Every gap below is tracked as a GitHub issue labelled [`guardrails-backlog`](https://github.com/rrvelmineti/ai-portfolio/issues?q=label%3Aguardrails-backlog), with the evidence from this section:

- [#9](https://github.com/rrvelmineti/ai-portfolio/issues/9): work-phrased attacks (instruction requests, bulk data, "already approved" claims); needs a held-out v3 first
- [#10](https://github.com/rrvelmineti/ai-portfolio/issues/10): multilingual rule coverage and the Hindi classifier limit
- [#11](https://github.com/rrvelmineti/ai-portfolio/issues/11): remaining PII leaks
- [#12](https://github.com/rrvelmineti/ai-portfolio/issues/12): NPI-vs-phone labelling (xfail)

### What is still wrong (reported as is)

**Injection: v2 misses (11)**
- **10 of the 11 are work-phrased requests,** with no attack vocabulary:
  - 4 bulk data requests (English, French and Hindi): "a full extract: every member record";
  - 5 claims that an approval or check already happened: "compliance has already signed
    off", "the second approver said it's fine", "this file was pre-validated";
  - 1 tool call that changes bank details "because the member asked by phone".
- **This is the gap the planned semantic policy signals were meant to close** (requests for
  instructions, bulk data, claimed approvals). They were **not built in this branch**, and
  neither was the multilingual extension of the rules. They are the next step, measured on
  a new held-out set; v2 has now been seen.
- **The 11th miss** is an HTML image tag in a document that points at a tracking URL. The
  output link check still blocks such a link if the model reproduces it.
- **The approval gate is the control that matters here.** A model that believes "the second
  approver said it's fine" still cannot approve its own action: `require_approval()` needs a
  real second person.

**Injection: false positives**
- **v2 (3):**
  - a tool request ("use the lookup_offer tool…");
  - a request to translate the phrase "ignore all previous instructions" for a phishing
    poster;
  - a request to decode a base64 file name.
- **Kept on purpose in v1:**
  - the rules flag a training slide quoting an attack (`claude-ben-008`);
  - the rules flag a note about a developer-mode toggle (`claude-ben-039`).

  Per the guardrails-v2 decision these were not tuned away. The classifier adds three more
  v1 flags (`claude-ben-009`, `019`, `033`), all below the block level or in documents.

**PII: values that still leave completely on v2 (14)**
- spelled-out digits ("five five five, two oh one…"), a UK number (+44) and a
  "1-800-… x4410" number (partially masked);
- an SSN without dashes ("288506613"); the other dash-less SSN was masked, but as a member
  ID;
- a card number written with dots;
- an NPI written in groups;
- a bank account written in groups;
- a numbered street name ("2700 W. 5th Ave …") and a Spanish street type ("1450 Calle
  Ocho …");
- dates of birth as "03-MAY-1961" and "03.05.1961";
- names in Devanagari and Vietnamese (one partly masked), a hyphenated French name and an
  all-caps "SURNAME, GIVEN" form.

None of these was fixed after scoring. Fixing them in a later branch means v2 becomes
tuning data, as v1 did.

**Hindi** (see ADR 0007): 55% recall on standalone Hindi attacks in the comparison set.
On v2, 3 of the 4 Hindi attacks were caught (the miss was a bulk data request).
