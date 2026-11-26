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
from ai_platform_guardrails.policy import (
    ProductPolicy,
    load_policy,
    make_topic_input_check,
    make_topic_output_check,
)


def build_guard(
    *,
    policy: ProductPolicy | None = None,
    link_allowlist: Sequence[str] | None = None,
    classifier: Classifier | None = None,
    use_classifier: bool | None = None,
) -> GuardrailsGuard:
    """The standard guard.

    Input: topic policy, injection scan, then PII masking, canary and the untrusted-data
    rule. Output: canary leak, exfiltration links, topic policy, new PII.

    ``policy`` defaults to ``GUARDRAILS_POLICY`` (or an empty policy). Its
    ``link_allowlist`` lists the domains links and images in model output may point to;
    ``link_allowlist`` here overrides it.
    """
    pol = policy or load_policy()
    links = list(link_allowlist) if link_allowlist is not None else pol.link_allowlist
    guard = GuardrailsGuard(
        input_checks=[
            make_topic_input_check(pol),
            make_injection_check(classifier, use_classifier=use_classifier),
        ],
        after_mask=[add_canary_and_fence_rule],
        output_checks=[canary_check, make_link_check(links), make_topic_output_check(pol)],
    )
    guard.policy = pol
    return guard
