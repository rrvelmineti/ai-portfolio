"""Guardrails for ai-portfolio: PII masking (Presidio), prompt-injection defence,
topic policy and human approval. Fail closed: if a guardrail errors, the AI call is
blocked, never sent raw.

    from ai_platform_guardrails import install_guardrails, wrap_untrusted

    install_guardrails(app, auth, session_factory, policy_path="policies/claims.yaml")
    complete(f"Summarise:\\n{wrap_untrusted(file_text, 'file:claims.csv')}")
"""

from ai_platform_guardrails.approvals import ApprovalRequest, ApprovalService, ApprovalStatus
from ai_platform_guardrails.injection.output import check_links, check_tool_calls
from ai_platform_guardrails.injection.scanner import ScanResult, scan
from ai_platform_guardrails.injection.untrusted import wrap_untrusted
from ai_platform_guardrails.install import (
    Guardrails,
    build_guard,
    configure_guardrails,
    install_guardrails,
)
from ai_platform_guardrails.pii import (
    MappingExpired,
    MaskResult,
    mask_records,
    mask_text,
    privacy_report,
    unmask,
)
from ai_platform_guardrails.policy import ProductPolicy, load_policy

__all__ = [
    "ApprovalRequest",
    "ApprovalService",
    "ApprovalStatus",
    "Guardrails",
    "MappingExpired",
    "MaskResult",
    "ProductPolicy",
    "ScanResult",
    "build_guard",
    "check_links",
    "check_tool_calls",
    "configure_guardrails",
    "install_guardrails",
    "load_policy",
    "mask_records",
    "mask_text",
    "privacy_report",
    "scan",
    "unmask",
    "wrap_untrusted",
]
