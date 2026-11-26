"""Building and installing the guardrails guard."""

from __future__ import annotations

from collections.abc import Sequence

from ai_platform_guardrails.gateway_guard import GuardrailsGuard
from ai_platform_guardrails.injection.checks import (
    Classifier,
    add_canary_and_fence_rule,
    canary_check,
    make_injection_check,
    make_link_check,
)


def build_guard(
    *,
    link_allowlist: Sequence[str] = (),
    classifier: Classifier | None = None,
    use_classifier: bool | None = None,
) -> GuardrailsGuard:
    """The standard guard: injection scan, PII masking, canary, link and PII output checks.

    ``link_allowlist`` lists domains that markdown/HTML links and images in model output
    may point to; everything else is blocked as possible data exfiltration.
    """
    return GuardrailsGuard(
        input_checks=[make_injection_check(classifier, use_classifier=use_classifier)],
        after_mask=[add_canary_and_fence_rule],
        output_checks=[canary_check, make_link_check(list(link_allowlist))],
    )
