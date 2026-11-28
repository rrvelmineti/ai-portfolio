"""The file status machine (ADR 0009).

```
received -> masked -> mapped -> under_review -> rules_run -> issues_sent <-----------+
                        ^          |   ^                  \\-> ready_for_signoff -> live
                        +send back-+   +----- rejected sign-off (internal fix) ----+ |
                                                rejected sign-off (client resubmits)-+
```

- ``under_review -> mapped`` sends mappings back to the analyst and needs a reason.
- ``issues_sent`` and ``live`` are final. A client's corrected file is a *new* file with
  ``previous_file_id`` pointing at the old one, starting again at ``received``.
- Every move out of ``ready_for_signoff`` is a go-live sign-off, never a plain
  transition: approval -> ``live``; rejection (reason required) -> ``under_review``
  for an internal fix, or ``issues_sent`` when the client must resubmit.

The data-dependent guards (all mappings decided, zero blocker results, four-eyes) live
in ``service.py``; this module holds only the shape of the machine.
"""

from __future__ import annotations

from collections.abc import Mapping

from ai_platform_auth import Conflict

from claims_onboarding.models import FileStatus as S

TRANSITIONS: Mapping[S, frozenset[S]] = {
    S.RECEIVED: frozenset({S.MASKED}),
    # TODO(Spec 1 Phase 4): leaving MASKED will require a privacy report for the file.
    S.MASKED: frozenset({S.MAPPED}),
    S.MAPPED: frozenset({S.UNDER_REVIEW}),
    S.UNDER_REVIEW: frozenset({S.RULES_RUN, S.MAPPED}),
    S.RULES_RUN: frozenset({S.ISSUES_SENT, S.READY_FOR_SIGNOFF}),
    S.ISSUES_SENT: frozenset(),
    S.READY_FOR_SIGNOFF: frozenset({S.LIVE, S.UNDER_REVIEW, S.ISSUES_SENT}),
    S.LIVE: frozenset(),
}

# Transitions that need a written reason (stored in the audit row).
NEEDS_REASON: frozenset[tuple[S, S]] = frozenset({(S.UNDER_REVIEW, S.MAPPED)})

# Edges only the go-live sign-off may take (approval -> live, rejection -> the others).
SIGNOFF_ONLY: frozenset[tuple[S, S]] = frozenset(
    (S.READY_FOR_SIGNOFF, target) for target in TRANSITIONS[S.READY_FOR_SIGNOFF]
)

# Where a rejected sign-off may send the file.
REJECTION_TARGETS: frozenset[S] = frozenset({S.UNDER_REVIEW, S.ISSUES_SENT})

FINAL: frozenset[S] = frozenset(s for s, targets in TRANSITIONS.items() if not targets)


class InvalidTransition(Conflict):
    """The requested status change is not allowed from the file's current status (409)."""

    def __init__(self, current: S, target: S, why: str | None = None) -> None:
        self.current = current
        self.target = target
        allowed = ", ".join(sorted(TRANSITIONS[current])) or "none (final status)"
        super().__init__(
            why or f"cannot move a file from {current} to {target}; allowed: {allowed}"
        )


def check_transition(current: S, target: S) -> None:
    """Raise ``InvalidTransition`` unless ``current -> target`` is an edge of the machine."""
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(current, target)
