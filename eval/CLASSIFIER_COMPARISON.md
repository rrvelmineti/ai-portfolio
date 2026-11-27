## ML classifier comparison (guardrails-v2 selection, tuning data only)

Measured once, before guardrails-v2 was built, to choose the classifier (ADR 0007). Same
data, same 0.5 threshold, ONNX Runtime on CPU (4-core Xeon at 2.3 GHz, batch 1). Held-out v2
and the deepset test split were **not** used. Prompt Guard 2 was exported to ONNX (fp32
matches torch within 6e-8) and tokenised exactly as at release.

Data: CyberSecEval multilingual + English prompt-injection sets (Meta, MIT, PurpleLlama
`87219c62`); MASSIVE 1.1 benign prompts (Amazon, CC-BY-4.0, 300 per language); our dev
and held-out v1 sets; deepset train. "Standalone" = CyberSecEval attack types that are
attacks without a system prompt (ignore-previous, system mode, role-play, payload
splitting, token smuggling; n = en 80, fr 21, hi 80, de 14, es 17).

### Attacks caught

| Candidate | CSE standalone en / fr / hi / de / es | CSE all types en / fr / hi / de / es | v1 attacks | dev attacks | deepset train |
|---|---|---|---:|---:|---:|
| **guard-small int8** (chosen) | **78 / 76 / 55 / 64 / 82%** | 51 / 42 / 35 / 51 / 43% | 85% | 97% | 85% † |
| Prompt Guard 2 86M fp32 | 29 / 29 / 14 / 14 / 35% ‡ | 19 / 18 / 7 / 8 / 20% ‡ | 30% | 69% | 25% |
| Prompt Guard 2 86M int8 | 12 / 10 / 0 / 7 / 12% ‡ | 6 / 4 / 0 / 5 / 4% ‡ | 25% | 40% | 12% |
| Prompt Guard 2 22M int8 | 22 / 10 / 0 / 0 / 0% ‡ | 14 / 4 / 0 / 0 / 0% ‡ | 15% | 43% | 12% |
| **rules + guard-small** (chosen) | 78 / 76 / 55 / 64 / 82% | 51 / 42 / 35 / 51 / 43% | **90%** | **100%** § | 85% † |
| rules + PG2 86M fp32 | 30 / 29 / 15 / 14 / 35% ‡ | 20 / 18 / 7 / 8 / 20% ‡ | 60% | 100% § | 30% |
| rules + PG2 86M int8 | 21 / 19 / 1 / 7 / 24% ‡ | 11 / 10 / 1 / 8 / 10% ‡ | 52% | 100% § | 22% |
| rules + PG2 22M int8 | 26 / 19 / 1 / 0 / 24% ‡ | 16 / 10 / 0 / 3 / 10% ‡ | 50% | 100% § | 22% |
| guard-small + PG2 86M fp32 | 78 / 76 / 56 / 64 / 82% | 51 / 42 / 36 / 51 / 43% | 85% | 97% | 85% † |
| rules only (guardrails v1) | 19 / 14 / 1 / 0 / 24% | 9 / 8 / 0 / 3 / 10% | 42% | 100% § | 13% |

### False positives and cost

| Candidate | MASSIVE benign FP fr / hi / te / de / es / en | dev benign | v1 benign | latency p50/p95, 4 threads · 1 thread | memory with spaCy lg | image added |
|---|---|---:|---:|---|---:|---:|
| **guard-small int8** | 1 / 0 / 0 / 2 / 1 / 2% | 9% | 15% | 13/27 · 31/67 ms | 1.44 GB | ~400 MB |
| Prompt Guard 2 86M fp32 | 1 / 1 / 0 / 0 / 1 / 1% | 9% | 12% | 28/63 · 66/153 ms | 2.54 GB (peak 2.62) | ~1.19 GB |
| Prompt Guard 2 86M int8 | 0% in all six | 0% | 8% | 18/33 · 27/57 ms | 1.58 GB | ~450 MB |
| Prompt Guard 2 22M int8 | 0 / 0 / 0 / 0 / 0 / 1% | 0% | 10% | 9/19 · 11/30 ms | 1.23 GB | ~200 MB |
| **rules + guard-small** | 1 / 0 / 0 / 2 / 1 / 2% | 9% | 20% | +<1 ms | 1.44 GB | ~400 MB |
| rules + PG2 86M fp32 | 1 / 1 / 0 / 0 / 1 / 1% | 9% | 15% | +<1 ms | 2.54 GB | ~1.19 GB |
| rules + PG2 86M int8 | 0% in all six | 0% | 10% | +<1 ms | 1.58 GB | ~450 MB |
| rules + PG2 22M int8 | 0 / 0 / 0 / 0 / 0 / 1% | 0% | 12% | +<1 ms | 1.23 GB | ~200 MB |
| guard-small + PG2 86M fp32 | 2 / 1 / 0 / 2 / 2 / 3% | 17% | 22% | both models | 2.57 GB (peak 3.11) | ~1.49 GB |
| rules only | 0% in all six | 0% | 5% | <1 ms | 0.79 GB | 0 |

These are the classifier's own numbers at 0.5, before the approved thresholds (rules
0.70 / 0.40, classifier 0.80 / 0.70) were applied. "Image added" = about 110 MB of
runtime packages plus the model files. Memory = resident memory of one process with
spaCy lg loaded (spaCy alone: 0.79 GB).

**Inflation markers:**
- † guard-small was trained on deepset train.
- ‡ CyberSecEval is Meta's benchmark and Prompt Guard 2's training data is unlisted, so
  overlap is possible (it still lost).
- § the rules were developed against the dev set.
- v1 and dev were written by the same author as the rules (author bias, not training
  overlap). MASSIVE and CyberSecEval are not listed in guard-small's training data.
