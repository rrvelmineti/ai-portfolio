# Held-out guardrails evaluation set (v1): SEEN

> **Status: seen.** From the `guardrails-v2` branch on, this set is **tuning data**: it is used
> to extend rules and choose classifier thresholds. Its scores are no longer an honest estimate
> of accuracy on new text. The honest held-out set is now `eval/heldout_v2/`. The data files
> below are unchanged since the freeze (their hashes in `MANIFEST.sha256` are the originals);
> only this README changed.

A second, frozen test set for the guardrails. It exists to measure how well the detectors
work on text they were **not** developed against. The development corpus (`eval/pii`,
`eval/injection`) drove the Phase 0.5 detector fixes and gates CI. This set is scored and
reported only; it never gates CI.

## Contents

| File | What |
|---|---|
| `claude_samples.txt` | Source of truth: 60 PII lines (83 tagged values, 10 lines with no PII), 40 attacks (25 chat, 15 document), 40 benign look-alikes (20 chat, 20 document), in the inline-tag format of `ai_platform_guardrails.eval_tags` |
| `pii/texts_claude.jsonl` | PII samples with exact gold spans, generated from the source |
| `injection/attacks_claude.jsonl`, `injection/benign_claude.jsonl` | Injection samples, generated from the source |
| `MANIFEST.sha256` | SHA-256 of every file above, written in the freeze commit |

Regenerate or verify the JSONL with:

```bash
uv run python scripts/convert_tagged_samples.py eval/heldout/claude_samples.txt --check
```

All values are synthetic: 555 phone numbers, `example.*` domains, documentation IP ranges
(RFC 5737 / RFC 3849), published test card numbers and made-up IDs.

## Freeze rule

- The set was committed **before any scoring run**. The detectors were never run on it while
  it was being written.
- It must not be edited after the freeze commit. `MANIFEST.sha256` is checked by a test, so
  an edit fails CI and shows up in review.
- If a sample turns out to be mislabelled, don't fix it in place: add a correction note to
  `docs/guardrails-results.md` and, if needed, start a new held-out set.
- Detector fixes prompted by this set go in a separate branch. Once a detector is tuned
  against a sample, that sample is no longer held out, and the results doc must say so.

## Limits: how independent is this set?

**Not very, and that matters.** No human-written samples were included. Every sample was
written by the same assistant that built the detectors and had read their rules and the
development corpus. However carefully it tried to write new styles, it knows what the
detectors look for, so these numbers are probably **optimistic**.

The only independent check in this evaluation is the public dataset
(`deepset/prompt-injections`, injection only), scored by
`scripts/download_public_eval.py`. There is no independent PII check; a public PII
benchmark is listed as future work in ADR 0006.
