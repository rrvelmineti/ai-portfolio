"""The demo's one AI endpoint: a guarded, capped question to a free-tier model (ADR 0008).

Order matters for cost:

1. the caller has already passed login and the per-user/per-IP rate limit;
2. ``reserve_ai_call`` counts the call against every cap and commits, *before* the model
   call, so a slow or failing provider still uses up the reservation (the host's compute
   is spent either way);
3. the gateway runs guardrails (PII masking, injection blocking) and then the free-only
   ``demo-free`` alias. Only Gemini and Groq are in that chain.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ai_platform_gateway import (
    Gateway,
    GatewayError,
    GuardrailBlocked,
    PolicyViolation,
    get_gateway,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_demo.usage import UsageCaps, reserve_ai_call, usage_snapshot

DEMO_MODEL = "demo-free"
SYSTEM_PROMPT = (
    "You are a concise assistant in a public software demo. All data is synthetic. "
    "Answer in at most 120 words. Placeholders such as <PERSON_1> stand for masked "
    "personal data; keep them as they are."
)
MAX_OUTPUT_TOKENS = 400


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str
    provider: str
    model: str
    pii_masked: dict[str, int]
    flags: list[str]
    usage: dict[str, int]


class AskRefused(Exception):
    """The question was not answered. ``detail`` is safe to show to visitors."""

    def __init__(self, status_code: int, detail: str, reasons: list[str] | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.reasons = reasons or []


def ask(
    session_factory: sessionmaker[Session],
    user_id: uuid.UUID,
    question: str,
    caps: UsageCaps,
    *,
    gateway: Gateway | None = None,
    now: datetime | None = None,
) -> AskResponse:
    """Reserve one call, then ask the free model with guardrails on.

    Raises ``DemoLimitReached`` when a cap is hit and ``AskRefused`` when guardrails or
    the providers refuse.
    """
    with session_factory() as session, session.begin():
        reserve_ai_call(session, user_id, caps, now)
    try:
        result = (gateway or get_gateway()).complete(
            question,
            DEMO_MODEL,
            system=SYSTEM_PROMPT,
            max_tokens=MAX_OUTPUT_TOKENS,
            metadata={"product": "demo", "feature": "ask"},
        )
    except GuardrailBlocked as exc:
        raise AskRefused(422, "Blocked by the guardrails: this looks like a prompt-injection "
                         "attempt or unsafe content.", exc.reasons) from exc  # fmt: skip
    except PolicyViolation as exc:
        raise AskRefused(422, exc.refusal, [f"topic:{exc.topic}"]) from exc
    except GatewayError as exc:
        raise AskRefused(503, "The free AI providers are busy or not configured right now. "
                         "Please try again later.") from exc  # fmt: skip
    with session_factory() as session:
        usage = usage_snapshot(session, now)
    return AskResponse(
        answer=result.text,
        provider=result.provider,
        model=result.model,
        pii_masked=result.pii_entities,
        flags=[*result.guard_flags, *result.output_flags],
        usage=usage,
    )
