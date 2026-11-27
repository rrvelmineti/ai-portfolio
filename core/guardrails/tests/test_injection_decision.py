"""Rules + ML classifier decision rule, unit level and through the gateway guard."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from ai_platform_gateway import Gateway, GuardrailBlocked, GuardrailError
from ai_platform_gateway.testing import FakeProvider, completion, fake_gateway
from ai_platform_guardrails.evaluation import score_injection
from ai_platform_guardrails.events import configure_events
from ai_platform_guardrails.injection.decision import (
    CLASSIFIER_BLOCK,
    CLASSIFIER_FLAG,
    assess,
    decide,
)
from ai_platform_guardrails.injection.ml import (
    ClassifierScore,
    ModelUnavailable,
    get_prompt_classifier,
    set_prompt_classifier,
)
from ai_platform_guardrails.injection.untrusted import wrap_untrusted
from ai_platform_guardrails.install import build_guard

BENIGN = "Please summarise the attached claim for me."


class Scripted:
    """Classifier that returns a fixed probability (optionally reporting truncation)."""

    def __init__(self, p: float, truncated: bool = False) -> None:
        self.name = f"scripted-{p}"
        self.p, self.truncated = p, truncated

    def score(self, text: str) -> ClassifierScore:
        return ClassifierScore(self.p, 64 if self.truncated else 1, self.truncated)


class Broken:
    name = "broken"

    def score(self, text: str) -> ClassifierScore:
        raise AssertionError("never called")


@pytest.fixture(autouse=True)
def _no_audit() -> Iterator[None]:
    configure_events(None)
    yield
    configure_events(None)


def guarded() -> tuple[Gateway, FakeProvider]:
    p = FakeProvider("anthropic", [completion("ok")])
    return fake_gateway({"anthropic": p}, guard=build_guard(use_classifier=False)), p


def test_thresholds_are_the_approved_ones() -> None:
    assert (CLASSIFIER_BLOCK, CLASSIFIER_FLAG) == (0.80, 0.70)


@pytest.mark.parametrize(
    ("rules", "p", "context", "expected"),
    [
        (0.0, 0.0, "chat", "none"),
        (0.0, 0.69, "chat", "none"),
        (0.0, 0.70, "chat", "flag"),
        (0.0, 0.79, "chat", "flag"),
        (0.0, 0.80, "chat", "block"),
        (0.40, 0.0, "chat", "flag"),
        (0.70, 0.0, "chat", "block"),
        (0.0, 0.99, "untrusted", "flag"),  # documents are never blocked
        (0.95, 0.0, "untrusted", "flag"),
        (0.39, 0.69, "untrusted", "none"),
    ],
)
def test_decide(rules: float, p: float, context: str, expected: str) -> None:
    clf = ClassifierScore(p, 1, False)
    assert decide(rules, clf, context) == expected  # type: ignore[arg-type]


def test_decide_without_classifier_is_rules_only() -> None:
    assert decide(0.5, None, "chat") == "flag"
    res = assess(BENIGN, None, "chat")
    assert res.action == "none" and res.classifier_score is None and res.score == 0.0


def test_assess_reports_the_classifier_reason_and_truncation() -> None:
    res = assess(BENIGN, Scripted(0.9, truncated=True), "untrusted")
    assert res.action == "flag" and "ml_classifier" in res.reasons and res.truncated
    assert res.score == 0.9


def test_classifier_blocks_chat_the_rules_miss() -> None:
    set_prompt_classifier(Scripted(0.85))
    gw, provider = guarded()
    with pytest.raises(GuardrailBlocked) as info:
        gw.complete(BENIGN)
    assert "ml_classifier" in info.value.reasons and provider.requests == []
    assert info.value.score == 0.85


def test_classifier_flag_band_flags_and_logs_without_blocking() -> None:
    set_prompt_classifier(Scripted(0.75))
    gw, provider = guarded()
    res = gw.complete(BENIGN)
    assert res.guard_flags == ["injection_borderline"] and res.injection_score == 0.75
    assert len(provider.requests) == 1


def test_chat_text_around_a_fence_is_still_blocked_when_it_scores_high() -> None:
    set_prompt_classifier(Scripted(0.99))
    gw, provider = guarded()
    doc = wrap_untrusted("Totals for March.", "file:big.csv")
    with pytest.raises(GuardrailBlocked):  # the chat part itself scores 0.99
        gw.complete(f"Summarise:\n{doc}")
    assert provider.requests == []


def test_untrusted_flags_with_benign_chat() -> None:
    class DocOnly:
        name = "doc-only"

        def score(self, text: str) -> ClassifierScore:
            hit = "Totals" in text
            return ClassifierScore(0.99 if hit else 0.0, 64 if hit else 1, hit)

    set_prompt_classifier(DocOnly())
    gw, provider = guarded()
    doc = wrap_untrusted("Totals for March.", "file:big.csv")
    res = gw.complete(f"Summarise this file:\n{doc}")
    assert res.guard_flags == [
        "untrusted_unscanned:file:big.csv",
        "untrusted_injection:file:big.csv",
    ]
    assert len(provider.requests) == 1


def test_missing_model_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    from ai_platform_guardrails.injection import ml

    set_prompt_classifier(None)
    monkeypatch.setenv("GUARD_ML_CLASSIFIER", "true")
    monkeypatch.setenv("GUARD_MODEL_DIR", str(tmp_path))
    ml._default.cache_clear()
    gw, provider = guarded()
    with pytest.raises(GuardrailError, match="unavailable"):
        gw.complete(BENIGN)
    assert provider.requests == []
    with pytest.raises(ModelUnavailable):
        get_prompt_classifier()
    ml._default.cache_clear()


def test_disabled_classifier_means_rules_only(monkeypatch: pytest.MonkeyPatch) -> None:
    set_prompt_classifier(None)
    monkeypatch.setenv("GUARD_ML_CLASSIFIER", "false")
    gw, provider = guarded()
    assert gw.complete(BENIGN).injection_score == 0.0
    assert len(provider.requests) == 1


def test_injection_flag_mode_never_blocks_on_classifier() -> None:
    set_prompt_classifier(Scripted(0.99))
    gw, provider = guarded()
    res = gw.complete(BENIGN, injection="flag")
    assert "injection:ml_classifier" in res.guard_flags and len(provider.requests) == 1


def test_score_injection_uses_the_same_rule() -> None:
    attacks = [
        {"id": "a1", "context": "chat", "category": "x", "text": BENIGN},
        {"id": "a2", "context": "untrusted", "category": "x", "text": BENIGN},
    ]
    benign = [{"id": "b1", "context": "chat", "text": BENIGN}]
    s = score_injection(attacks, benign, Scripted(0.75))  # flag band
    assert (s.detected, s.missed, s.false_positive_ids) == (1, ["a1"], ["b1"])
    assert s.classifier == "scripted-0.75"
    rules_only = score_injection(attacks, benign, None)
    assert rules_only.detected == 0 and rules_only.classifier is None
    assert score_injection([], [], Broken()).attacks == 0
