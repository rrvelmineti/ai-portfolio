"""CI gate: the guardrails must meet their targets on the synthetic corpus (default model).

Runs in CI (not marked live): no network, the corpus is in eval/.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from ai_platform_guardrails.evaluation import (
    EntityScore,
    InjectionScore,
    evaluate_injection,
    evaluate_pii,
    load_jsonl,
    render_markdown,
    target_failures,
)
from ai_platform_guardrails.pii.config import spacy_model_name

ROOT = Path(__file__).resolve().parents[3]
EVAL = ROOT / "eval"


def test_corpus_size_requirements() -> None:
    texts = load_jsonl(EVAL / "pii" / "texts.jsonl")
    table = json.loads((EVAL / "pii" / "claims_table.json").read_text(encoding="utf-8"))
    assert len(texts) + len(table["rows"]) >= 200
    kinds = {t["kind"] for t in texts}
    assert {"free_text", "email", "letter", "tricky"} <= kinds
    attacks = load_jsonl(EVAL / "injection" / "attacks.jsonl")
    benign = load_jsonl(EVAL / "injection" / "benign.jsonl")
    assert len(attacks) >= 30 and len(benign) >= 30
    cats = {a["category"] for a in attacks}
    assert {"direct", "encoded", "multilingual", "extraction", "exfiltration", "tool_abuse",
            "indirect_document", "indirect_csv"} <= cats  # fmt: skip


@pytest.mark.real_classifier
def test_targets_met_on_default_model() -> None:
    assert spacy_model_name() == "en_core_web_lg", "targets are enforced on the default model"
    pii = evaluate_pii(EVAL)
    inj = evaluate_injection(EVAL)
    failures = target_failures(pii, inj)
    assert failures == [], "\n" + render_markdown(pii, inj, spacy_model_name())


def test_target_failures_reported() -> None:
    weak = {"US_SSN": EntityScore(tp=9, fn=1)}
    inj = InjectionScore(
        attacks=10, detected=9, benign=10, false_positives=2, missed=["a"],
        false_positive_ids=["b", "c"], by_category={"direct": (9, 10)},
    )  # fmt: skip
    failures = target_failures(weak, inj)
    assert "US_SSN recall 90.0% < 100%" in failures
    assert any("injection detection" in f for f in failures)
    assert any("false positives" in f for f in failures)
    md = render_markdown(weak, inj, "m")
    assert "| US_SSN | 10 | 9 | 1 | 0 | 90.0% | 100.0% | 100% ❌ |" in md
    assert "injection missed: a" in md
    assert EntityScore().recall == 1.0 and EntityScore().precision == 1.0
    empty = InjectionScore(0, 0, 0, 0, [], [], {})
    assert empty.detection_rate == 1.0 and empty.false_positive_rate == 0.0


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "guardrails_report", ROOT / "scripts" / "guardrails_report.py")  # fmt: skip
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.real_classifier
def test_report_script_writes_results(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "results.md"
    assert _load_script().main(["--check", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "# Guardrails evaluation results" in text and "honest history" in text
    assert "All targets met." in capsys.readouterr().out
