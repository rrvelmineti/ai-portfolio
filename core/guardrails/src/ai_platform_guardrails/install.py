"""Building and installing the guardrails guard."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_platform_auth import Auth
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_guardrails.approvals.service import ApprovalService
from ai_platform_guardrails.events import configure_events
from ai_platform_guardrails.gateway_guard import GuardrailsGuard, install_guard
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


@dataclass
class Guardrails:
    """Handles to what ``install_guardrails`` / ``configure_guardrails`` set up."""

    guard: GuardrailsGuard
    policy: ProductPolicy
    approvals: ApprovalService


def configure_guardrails(
    session_factory: sessionmaker[Session] | None = None,
    *,
    policy: ProductPolicy | None = None,
    policy_path: Path | str | None = None,
    approvals: ApprovalService | None = None,
    use_classifier: bool | None = None,
) -> Guardrails:
    """Install the guard on the gateway, the trace redactor and audit events (no web app)."""
    pol = policy or load_policy(policy_path)
    guard = install_guard(build_guard(policy=pol, use_classifier=use_classifier))
    configure_events(session_factory)
    return Guardrails(guard, pol, approvals or ApprovalService())


def install_guardrails(
    app: Any,  # noqa: ANN401 - a FastAPI app
    auth: Auth,
    session_factory: sessionmaker[Session],
    *,
    policy: ProductPolicy | None = None,
    policy_path: Path | str | None = None,
    approvals: ApprovalService | None = None,
    use_classifier: bool | None = None,
) -> Guardrails:
    """``configure_guardrails`` plus the ``/approvals`` router (uses ``auth``'s roles)."""
    from ai_platform_guardrails.approvals.router import build_approvals_router

    g = configure_guardrails(session_factory, policy=policy, policy_path=policy_path,
                             approvals=approvals, use_classifier=use_classifier)  # fmt: skip
    app.include_router(build_approvals_router(auth, g.approvals))
    return g
