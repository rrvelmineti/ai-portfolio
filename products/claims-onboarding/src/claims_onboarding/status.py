"""The file status machine (ADR 0009).

```
received -> masked -> mapped -> under_review -> rules_run -> issues_sent
                        ^            |                    \\-> ready_for_signoff -> live
                        +--send back-+
```

- ``under_review -> mapped`` sends mappings back to the analyst and needs a reason.
- ``issues_sent`` and ``live`` are final. A client's corrected file is a *new* file with
  ``previous_file_id`` pointing at the old one, starting again at ``received``.
- ``live`` is reached only through a go-live sign-off, never a plain transition.

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
    S.READY_FOR_SIGNOFF: frozenset({S.LIVE}),
    S.LIVE: frozenset(),
}

# Transitions that need a written reason (stored in the audit row).
NEEDS_REASON: frozenset[tuple[S, S]] = frozenset({(S.UNDER_REVIEW, S.MAPPED)})

# Only the sign-off flow may make a file live.
SIGNOFF_ONLY: frozenset[S] = frozenset({S.LIVE})

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
