"""One decision rule for prompt injection, shared by the live guard and the evaluation.

Two layers with their own thresholds (approved for guardrails-v2, ADR 0007):

| Layer | Block (chat only) | Flag |
|---|---|---|
| Rules (``scanner.scan``) | 0.70 | 0.40 |
| ML classifier (``ml.PromptClassifier``) | 0.80 | 0.70 |

Chat input is blocked when either layer reaches its block threshold, and flagged when
either reaches its flag threshold. Content inside ``wrap_untrusted`` fences is never
blocked, only flagged. The thresholds were chosen on held-out v1 and the deepset train
split only (``scripts/tune_injection_thresholds.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ai_platform_guardrails.injection.ml import ClassifierScore, PromptClassifier
from ai_platform_guardrails.injection.scanner import (
    BLOCK_THRESHOLD as RULES_BLOCK,
)
from ai_platform_guardrails.injection.scanner import (
    BORDERLINE_THRESHOLD as RULES_FLAG,
)
from ai_platform_guardrails.injection.scanner import scan

CLASSIFIER_BLOCK = 0.80
CLASSIFIER_FLAG = 0.70

Action = Literal["none", "flag", "block"]
Context = Literal["chat", "untrusted"]


@dataclass(frozen=True)
class Decision:
    action: Action
    rules_score: float
    classifier_score: float | None  # None when the classifier is disabled
    reasons: list[str] = field(default_factory=list)
    truncated: bool = False  # classifier scanned only the first MAX_WINDOWS windows

    @property
    def score(self) -> float:
        """Single number for logs and results: the higher of the two layers."""
        return max(self.rules_score, self.classifier_score or 0.0)


def decide(rules_score: float, clf: ClassifierScore | None, context: Context) -> Action:
    p = clf.score if clf else 0.0
    if context == "chat" and (rules_score >= RULES_BLOCK or p >= CLASSIFIER_BLOCK):
        return "block"
    if rules_score >= RULES_FLAG or p >= CLASSIFIER_FLAG:
        return "flag"
    return "none"


def assess(text: str, classifier: PromptClassifier | None, context: Context) -> Decision:
    """Score ``text`` with both layers and apply the decision rule."""
    rules = scan(text)
    clf = classifier.score(text) if classifier is not None else None
    action = decide(rules.score, clf, context)
    reasons = list(rules.reasons)
    if clf is not None and clf.score >= CLASSIFIER_FLAG:
        reasons.append("ml_classifier")
    return Decision(
        action=action,
        rules_score=rules.score,
        classifier_score=clf.score if clf else None,
        reasons=reasons,
        truncated=bool(clf and clf.truncated),
    )
