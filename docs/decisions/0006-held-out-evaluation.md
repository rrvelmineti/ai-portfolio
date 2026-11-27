# 0006. Held-out and public evaluation of the guardrails

- Status: Accepted
- Date: 2026-11-27

## Context

Phase 0.5 met every guardrails target on its synthetic development corpus. That corpus
also drove the detector fixes (documented in `eval/RESULTS_NOTES.md`), so meeting the
targets on it says little about text the detectors have never seen. We needed a measure of
how far the development numbers can be trusted, without turning that measure into another
set to tune against.

## Decision

1. **A frozen held-out set** (`eval/heldout`): 60 PII lines (83 values), 40 attacks and 40
   benign look-alikes, hand-written in an inline-tag format and converted to exact spans by
   `scripts/convert_tagged_samples.py`.
   - It was committed before any scoring run. A SHA-256 manifest is checked by a test, so
     any later edit fails CI.
   - Detector fixes prompted by it go in a separate branch, which must name the samples
     it was tuned against; those samples then stop counting as held out.
2. **A public, independent check**: `deepset/prompt-injections`, test split (116 rows).
   - Downloaded by `scripts/download_public_eval.py`, pinned to a dataset revision and a
     SHA-256 of the rows. Licence: Apache-2.0 (dataset card).
   - Never committed, and not downloaded in CI, so CI does not depend on the network.
3. **Report only.** Held-out and public numbers appear side by side with the development
   numbers in `docs/guardrails-results.md`. CI prints the held-out comparison but gates
   only on the development corpus. Gating on the held-out set would push the next
   detector change to tune against it, and then it would no longer be held out.
4. **Misses are listed by sample id, never by text**, the same rule as for logs.

## Limits

- **The held-out set is not independent.** No human-written samples were included; the
  same assistant that built the detectors wrote it, knowing their rules. Its numbers are
  probably optimistic. The public dataset is the only independent check, and only for
  injection.
- **Small sets.** 40 attacks and 83 PII values give wide error bars: one sample moves a
  rate by 2.5 points, and some entity types have only 3 gold values.
- **Public labels are not ours.** Some deepset "injections" are role-play or task
  requests we would allow. We report the score as labelled and don't relabel.

## Future work

- **A public PII benchmark.** We skipped it: the obvious candidate, the `ai4privacy`
  PII-masking datasets, has a licence that varies by version, and some versions restrict
  commercial use, so each version would need a licence review first. Its data is
  synthetic, so data privacy is not the blocker.
- Human-written held-out samples from someone who has not read the detector rules.

## Consequences

- `docs/guardrails-results.md` now shows the gap: injection detection was 100% on
  development, 42.5% held-out and 10% public, with false positives staying low.
- The development targets remain the CI gate; they now read as "no regressions on known
  cases", not as an accuracy claim.
- Any future detector work is measured on all three sets and has to state which samples
  it tuned on.
- The infrastructure ADR in Phase 0.6 is numbered 0007.
