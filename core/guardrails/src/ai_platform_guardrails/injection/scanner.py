"""Input scanner: normalisation + decoded variants + weighted rules -> score and reasons."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_platform_guardrails.injection.normalize import normalise
from ai_platform_guardrails.injection.rules import match_rules

BLOCK_THRESHOLD = 0.7
BORDERLINE_THRESHOLD = 0.4
OBFUSCATION_BONUS = 0.15
ENCODED_PAYLOAD_WEIGHT = 0.3  # extra signal when a rule only matches after decoding


@dataclass(frozen=True)
class ScanResult:
    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.score >= BLOCK_THRESHOLD

    @property
    def borderline(self) -> bool:
        return BORDERLINE_THRESHOLD <= self.score < BLOCK_THRESHOLD


def _combine(weights: list[float]) -> float:
    remaining = 1.0
    for w in weights:
        remaining *= 1.0 - w
    return 1.0 - remaining


def scan(text: str) -> ScanResult:
    """Score ``text`` for prompt-injection intent (0 = benign, 1 = certain)."""
    if not text or not text.strip():
        return ScanResult(0.0)
    norm = normalise(text)
    hits: dict[str, float] = {}
    for rule in match_rules(norm.text):
        hits[rule.name] = max(hits.get(rule.name, 0.0), rule.weight)
    decoded: set[str] = set()
    for kind, variant in norm.variants:
        for rule in match_rules(variant):
            if rule.name not in hits:
                hits[rule.name] = rule.weight
                decoded.add(kind)
    weights = list(hits.values())
    reasons = sorted(hits)
    if decoded:
        weights.append(ENCODED_PAYLOAD_WEIGHT)
        reasons += [f"encoded_payload:{k}" for k in sorted(decoded)]
    if norm.obfuscated and hits:
        weights.append(OBFUSCATION_BONUS)
        reasons.append("obfuscation")
    return ScanResult(round(_combine(weights), 4), reasons)
