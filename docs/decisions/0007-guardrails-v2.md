# 0007. Guardrails v2: local ML injection classifier, per-layer thresholds, PII fixes

- Status: Accepted
- Date: 2026-11-27

## Context

The held-out evaluation (ADR 0006) showed the rules-only injection scanner catching 35/35
development attacks but only 17/40 held-out and 6/60 public ones, missing every German
attack and most attacks phrased as ordinary work. PII detection leaked written-out dates of
birth, obfuscated emails, rural and PO Box addresses and IDs with separators.

Guardrails v2 had to close those gaps without fooling ourselves:
- **Held-out v1 became tuning data.** A new held-out v2 was frozen before any v2 change
  (and before any classifier ran) and is scored once, at the end.
- **Public data:** tune only on the deepset train split; report on the test split.

## Decision

### 1. A local ML classifier, behind an interface

- **`PromptClassifier` protocol** (`injection/ml.py`), with Horizon-Labs
  **`prompt-injection-guard-small`** (int8 ONNX, mmBERT-small backbone) as the
  implementation.
- **Runtime:** `onnxruntime==1.32.0` and `tokenizers==0.23.3` only. No torch, no
  transformers, no pickle files and no remote code.
- **What runs and how:**
  - every chat message and the contents of every `wrap_untrusted` block;
  - 512-token windows with a 128-token overlap, where the highest window counts;
  - after 64 windows the rest is not scanned and is flagged as `untrusted_unscanned`.
- **Fail closed:** if the classifier is enabled (`GUARD_ML_CLASSIFIER`, default on) but its
  files are missing or fail their checks, the call is blocked.

### 2. Supply chain

- **Pinned:** revision `0c8139d109994806ff404b5b693ad5d63629b9ba` (v2.7).
- **Five files, each checked against its pinned size and the Hub's SHA-256** before use:
  the ONNX model, `tokenizer.json`, `config.json` (label names), `LICENSE` and `NOTICE`.
- **Download:** `scripts/download_guard_model.py`. CI caches the files keyed on the
  revision.
- **Vendor risk (recorded honestly):**
  - Horizon Labs is a small vendor; its Hub account was about two months old at adoption.
  - It shipped three releases in two days (v2.6, v2.6.1, v2.7).
  - Its own `SHA256SUMS` file was stale across those releases, which is why we pin the
    Hub's LFS hashes instead.
- **How we'd swap it:** implement `PromptClassifier` (one `score(text)` method) and change
  the factory, or call `set_prompt_classifier()`. The decision rule and the evaluation are
  model-agnostic.
- **Licence:**
  - The model is **Apache-2.0** (commercial use allowed).
  - Its weights and tokenizer derive from mmBERT-small (**MIT**). Its `NOTICE` reproduces
    the MIT notice.
  - Both files travel with the model, in `.models/` and in the CI cache, and must ship
    next to it in any image.
  - The vendor states it trained only on permissively licensed data and excluded every
    benchmark it reports (including deepset test). We can't verify that. Its card does
    list **deepset train** as training data, so our numbers on that split are inflated.

### 3. Per-layer thresholds (chosen on held-out v1 + deepset train only)

| Layer | Block (chat only) | Flag |
|---|---:|---:|
| Rules | 0.70 (unchanged) | 0.40 (unchanged) |
| guard-small | **0.80** | **0.70** |

- **Shared rule:** `injection/decision.py` holds one decision rule used by both the guard and
  the evaluation.
- **Chat:** blocked at either block threshold, flagged and logged between flag and block.
  The optional LLM second opinion (`GUARD_LLM_CLASSIFIER`) can escalate a flag to a block.
- **`wrap_untrusted` content** is only ever flagged.
- **Why 0.80:** the lowest classifier threshold where v1 benign samples blocked stay under
  5% (1/40, and that one is blocked by the rules).
- **Why 0.70 for flagging:** same v1 attack detection as 0.50 (35/40) with fewer benign
  flags (5/40 vs 8/40).
- **Why not one threshold on max(rules, classifier):** above 0.85 it would also raise the
  rules' own block level and stop blocking two dev attacks the rules score 0.80.
- **The curve:** `docs/injection-thresholds.md`.
- **Known false positives, kept on purpose (not tuned away):**
  - the rules flag a training slide that quotes an attack (`claude-ben-008`);
  - the rules flag a developer-mode toggle note (`claude-ben-039`).

### 4. Not everything is a detection problem

Some attacks are only attacks relative to a system prompt ("sort these numbers in
descending order instead", "translate this instead"). No classifier can see that intent
from the text alone: CyberSecEval's English set was only 51% detectable by the best model.
Those task hijacks are handled by **architecture, not detection**:
- fencing untrusted content with a standing instruction;
- tool allowlists;
- human approval for risky actions;
- least privilege for what a product's model can do at all.

### 5. PII

Section filled in by the PII commits of this branch (see below).

## Considered

### Classifier candidates

Measured on tuning data only. The full comparison table, with inflation markers, is
reproduced in `docs/guardrails-results.md` (from `eval/CLASSIFIER_COMPARISON.md`).

| Candidate | Standalone attacks en / fr / hi | v1 attacks / v1 benign FP | p50 latency (4 thr) | memory with spaCy | image added |
|---|---|---|---:|---:|---:|
| **guard-small int8** | 78 / 76 / 55% | 85% / 15% | 13 ms | 1.44 GB | ~400 MB |
| Prompt Guard 2 86M fp32 | 29 / 29 / 14% ‡ | 30% / 12% | 28 ms | 2.54 GB | ~1.19 GB |
| Prompt Guard 2 86M int8 | 12 / 10 / 0% ‡ | 25% / 8% | 18 ms | 1.58 GB | ~450 MB |
| Prompt Guard 2 22M int8 | 22 / 10 / 0% ‡ | 15% / 10% | 9 ms | 1.23 GB | ~200 MB |
| ProtectAI deberta-v3 v2 | not measured (English-only model) | 83% / 45% (dev benign FP 15/35) | 28 ms | – | ~850 MB |

‡ CyberSecEval is Meta's benchmark; Prompt Guard 2's training data is unlisted.

**Combinations:**
- rules + guard-small beats guard-small alone (v1 90% vs 85%, dev 100%).
- guard-small + Prompt Guard 2 adds false positives (dev 17%, v1 22%) with no recall gain.

### Prompt Guard 2 licence (Llama 4 Community License), had it been chosen

- **Commercial use** is allowed below 700M monthly active users.
- **Distributing it** (including inside a container image given to anyone else) requires:
  - a copy of the licence;
  - a `NOTICE` file with "Llama 4 is licensed under the Llama 4 Community License,
    Copyright © Meta Platforms, Inc. All Rights Reserved.";
  - a prominent "Built with Llama";
  - a name starting with "Llama" for any model fine-tuned from it.
- **Meta's Acceptable Use Policy** applies, including its clauses on health data and
  professional practice.
- **Caching an ONNX export in our own CI** is reproduction, not distribution to third
  parties, so it is permitted, with the licence and notice stored alongside. This is our
  reading, not legal advice.
- **Delivery would have been:**
  - a one-off export script with pinned versions (torch 2.15.0+cpu, transformers 4.51.3 /
    tokenizers 0.21.1, onnx 1.23.2), run once or as a manual CI job;
  - a recorded SHA-256 of the exported file;
  - no torch or transformers in the runtime image.

## Known limits

- **Hindi.** guard-small catches **55%** of standalone CyberSecEval attacks in Hindi (n=80),
  against 78% in English and 76% in French. No available candidate did better: the best
  Prompt Guard 2 variant reached 14%, and its int8 build 0%. **Future work:** a
  Hindi-capable classifier, or fine-tuning on permissively licensed Hindi attack data, then
  re-measuring on the same split.
- **Telugu attacks** couldn't be measured: no public attack set exists. Telugu benign
  prompts (MASSIVE) produced 0% false positives.
- **System-prompt-relative task hijacks** are left to architecture (section 4).
- **Small sets.** v1 and v2 each have 40 attacks, so one sample is 2.5 points.
- **Vendor risk** (section 2). The pin and the hash checks protect against silent
  changes, not against a flawed model.
- **Memory.** One process with spaCy lg and guard-small uses about **1.5 GB**. Phase 0.6 has
  to size containers per worker accordingly.

## Consequences

- **CI** downloads (once per revision) and verifies the model, and runs the real-model tests.
  The development gate now measures rules + classifier together, as production runs them.
- **New products** get the classifier automatically through `install_guardrails`. Turning it
  off (`GUARD_ML_CLASSIFIER=false`) falls back to rules only and is logged in the results.
- **Infrastructure's ADR** becomes 0008.
