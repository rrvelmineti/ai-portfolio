"""Partial-leak metric: what actually stays visible, whatever type masked it."""

from __future__ import annotations

from ai_platform_guardrails.evaluation import (
    EntityScore,
    EvalSet,
    InjectionScore,
    cell_coverage,
    render_comparison,
    render_markdown,
    score_pii_samples,
    span_coverage,
)
from ai_platform_guardrails.pii.engine import Detection

TEXT = "Mail to 12 Elm St, Cody, WY 82414 today"
GOLD = (8, 33)  # "12 Elm St, Cody, WY 82414"


def test_span_fully_masked_even_by_another_type() -> None:
    found = [Detection("ADDRESS", 8, 17, 0.7), Detection("PERSON", 19, 33, 0.8)]
    assert span_coverage(TEXT, *GOLD, found) == "masked"  # punctuation/space gaps don't count


def test_span_partial_and_full_leak() -> None:
    assert span_coverage(TEXT, *GOLD, [Detection("ADDRESS", 8, 17, 0.7)]) == "partial"
    assert span_coverage(TEXT, *GOLD, [Detection("PERSON", 0, 4, 0.9)]) == "leak"
    assert span_coverage(TEXT, *GOLD, []) == "leak"


def test_cell_coverage() -> None:
    assert cell_coverage("555-201-4482", "<PHONE_1>") == "masked"
    assert cell_coverage("Ana Ruiz", "<PERSON_1> Ruiz") == "partial"
    assert cell_coverage("Ana Ruiz", "Ana Ruiz") == "leak"
    assert cell_coverage("Ana Ruiz", "Ana Ruiz (checked)") == "leak"
    assert cell_coverage("-", "-") == "masked"  # nothing identifying in the cell


def test_score_pii_samples_counts_leaks_per_entity() -> None:
    samples = [
        {"id": "s1", "text": "Call Ana Ruiz on 555-201-4482 now.",
         "entities": [{"type": "PERSON", "start": 5, "end": 13},
                      {"type": "PHONE", "start": 17, "end": 29}]},
        {"id": "s2", "text": "Write to kim dot park at example dot com please",
         "entities": [{"type": "EMAIL", "start": 9, "end": 40}]},
    ]  # fmt: skip
    scores = score_pii_samples(samples)
    assert scores["PERSON"].masked == 1 and scores["PHONE"].masked == 1
    assert scores["EMAIL"].masked == 1 and scores["EMAIL"].tp == 1


def test_leaks_rendered_in_both_reports() -> None:
    sc = EntityScore(tp=1, fn=1, masked=1, partial_leaks=1, partial_ids=["x-1"],
                     full_leaks=1, leak_ids=["x-2"])  # fmt: skip
    inj = InjectionScore(1, 1, 1, 0, [], [], {})
    md = render_markdown({"ADDRESS": sc}, inj, "m")
    assert "| ADDRESS | 3 | 1 | 1 | 1 |" in md
    sets = [EvalSet("Dev", {"ADDRESS": sc}, inj), EvalSet("Held", {"ADDRESS": sc}, inj),
            EvalSet("Public", None, inj)]  # fmt: skip
    cmp = render_comparison(sets)
    assert "| ADDRESS | 1 / 1 of 3 | 1 / 1 of 3 | not measured |  |" in cmp
    assert "- ADDRESS partial leak: x-1" in cmp and "- ADDRESS full leak: x-2" in cmp
