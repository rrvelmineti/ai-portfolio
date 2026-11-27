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

### 5. PII: real leaks first

- **Obfuscated emails** (`name dot x at domain dot tld`, `(at)`/`[at]`/`{at}`) are masked
  as EMAIL. A stop-list keeps prose like "at the dot com era" out.
- **Addresses:**
  - PO Box/POB, rural and highway-contract routes, military APO/FPO/DPO, and unit forms;
  - a bug that cut addresses at "Dr." is fixed;
  - a **merge step** extends an address over a following unit or city/state/ZIP and absorbs
    overlapping detections, so a partial address is never left.
- **Dates of birth written out** (ordinals, word ordinals, `'yy`, French/German/Spanish
  months) count when a DOB word is near (also `geb.`, `né`, `nacido`, `जन्मतिथि`).
- **IDs with separators** (spaces, dashes, `#`, either case) for member, policy and claim
  numbers. The guardrails README shows how tenants add their own formats.
- **A partial-leak metric:** every gold value is counted as fully masked, a partial leak or
  a full leak, with masking by any type counting.

### 6. Evaluation hygiene

- **Held-out v1 was marked seen** and used for tuning.
- **Held-out v2 was frozen** before any v2 change (source hash
  `b0649dec9fe63ca8f19ce68359f1e1ff8ef57272469f2bcf1ed542db9eebe3c3`) and scored once, at
  the end, together with the deepset test split and with the guardrails v1 code as a
  baseline on the same data.
- **Disclosed:** 8 of v2's 60 PII lines were typed from memory as probe inputs while the PII
  fixes were developed. No code changed in response to them, but they are not cleanly
  unseen. The results show v2 PII with and without them.

## Results (single final scoring; details in `docs/guardrails-results.md`)

| | Guardrails v1 | Guardrails v2 |
|---|---:|---:|
| v2 attacks caught (unseen) | 5/40 (12.5%) | **29/40 (72.5%)** |
| v2 benign wrongly flagged | 2/40 | 3/40 (7.5%) |
| deepset test attacks caught (public; guard-small trained on deepset train) | 6/60 | **24/60** |
| deepset test benign wrongly flagged | 0/56 | 0/56 |
| v2 PII values found, right type (without the 8 probed lines) | 38/65 | **50/65** (42/56) |
| v2 PII values fully leaked, any type (without the 8 probed lines) | – | 14/65 (13/56), plus 2 partial |
| dev targets (CI gate) | met | met |

**Not built in this branch:**
- the semantic policy signals for work-phrased attacks (requests for instructions, bulk
  data, claimed approvals);
- the multilingual extension of the rules.

10 of the 11 v2 misses are exactly those work-phrased requests. They are the next step,
measured on a new held-out set, because v2 has now been seen.

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
- **Work-phrased attacks** (bulk data, claimed approvals) mostly pass detection (section 6).
  The approval gate is the control that holds: a second person must approve.
- **PII formats still leaking on v2:** spelled-out and international phones, an SSN without
  dashes, dotted card numbers, grouped NPIs and bank numbers, numbered or Spanish street
  names, "03-MAY-1961" and "03.05.1961" dates of birth, and non-Latin or all-caps names.
- **Memory.** One process with spaCy lg and guard-small uses about **1.5 GB**. Phase 0.6 has
  to size containers per worker accordingly.

## Consequences

- **CI** downloads (once per revision) and verifies the model, and runs the real-model tests.
  The development gate now measures rules + classifier together, as production runs them.
- **New products** get the classifier automatically through `install_guardrails`. Turning it
  off (`GUARD_ML_CLASSIFIER=false`) falls back to rules only and is logged in the results.
- **Infrastructure's ADR** becomes 0008.
