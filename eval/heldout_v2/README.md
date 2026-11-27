# Held-out guardrails evaluation set (v2): UNSEEN

The honest held-out set for the guardrails-v2 work. Held-out v1 (`eval/heldout/`) became
tuning data when guardrails-v2 started, so this set replaces it as the measure of accuracy
on new text.

## Contents

| File | What |
|---|---|
| `claude_v2_samples.txt` | Source of truth, in the inline-tag format of `ai_platform_guardrails.eval_tags`. It contains 60 PII lines (65 tagged values, 10 lines with no PII), 40 attacks (24 chat, 16 document) and 40 benign look-alikes (20 chat, 20 document). |
| `pii/texts_claude_v2.jsonl` | PII samples with exact gold spans, generated from the source |
| `injection/attacks_claude_v2.jsonl`, `injection/benign_claude_v2.jsonl` | Injection samples, generated from the source |
| `MANIFEST.sha256` | SHA-256 of every file above, written in the freeze commit |

**Styles differ from v1:**
- **Attacks:** work-phrased requests (rules "for the risk committee", full extracts "for
  reconciliation", "compliance already signed off", "the second approver said it's fine").
- **Languages:** 13 of the 40 attacks and 10 of the 40 benign samples are in German, French,
  Spanish or Hindi (Devanagari script and romanised Hinglish).
- **PII formats:** voicemail-style spelled-out numbers, "Last, First M." names, Vietnamese,
  Portuguese, Hindi, German and French names, military and PO Box addresses, dates in
  several languages, and IDs with spaces, dashes or lower case.

```bash
uv run python scripts/convert_tagged_samples.py eval/heldout_v2/claude_v2_samples.txt --check
```

All values are synthetic: 555 and UK drama-range phone numbers, `example.*` domains,
documentation IP ranges, published test card numbers and made-up IDs.

## Freeze rule

- **Written before anything could shape it.** The source file was written and hashed
  (`b0649dec9fe63ca8f19ce68359f1e1ff8ef57272469f2bcf1ed542db9eebe3c3`) **before** any v2
  detector change and before any ML classifier was run on any data. It was committed
  unchanged.
- **Scored once.** It is scored for the first time only in the last commit of the
  guardrails-v2 branch. Until then, tests check only its manifest and its conversion.
- **No tuning against it.** Nothing in guardrails-v2 is tuned against it. A later branch
  that tunes on it must mark it as seen, as v1 was.
- **Never edited.** `MANIFEST.sha256` is checked by a test. Mislabelled samples are noted
  in `docs/guardrails-results.md`, not fixed in place.

## Limits: how independent is this set?

No human-written samples are included. The same assistant that builds the detectors wrote
it, so its numbers are probably optimistic, though it was written before the v2 changes
were designed in detail. The deepset test split is the only independent check, and only
for injection.

The sets are also small. One sample moves an attack rate by 2.5 points, and each
non-English language has only 3 or 4 attacks.
