"""The file status machine on its own (no database)."""

from __future__ import annotations

import itertools

import pytest
from claims_onboarding.models import FileStatus as S
from claims_onboarding.status import (
    FINAL,
    NEEDS_REASON,
    REJECTION_TARGETS,
    SIGNOFF_ONLY,
    TRANSITIONS,
    InvalidTransition,
    check_transition,
)

ALLOWED = {
    (S.RECEIVED, S.MASKED),
    (S.MASKED, S.MAPPED),
    (S.MAPPED, S.UNDER_REVIEW),
    (S.UNDER_REVIEW, S.RULES_RUN),
    (S.UNDER_REVIEW, S.MAPPED),  # send back (needs a reason)
    (S.RULES_RUN, S.ISSUES_SENT),
    (S.RULES_RUN, S.READY_FOR_SIGNOFF),
    (S.READY_FOR_SIGNOFF, S.LIVE),  # sign-off approval
    (S.READY_FOR_SIGNOFF, S.UNDER_REVIEW),  # rejected sign-off: internal fix
    (S.READY_FOR_SIGNOFF, S.ISSUES_SENT),  # rejected sign-off: client resubmits
}


def test_every_status_has_an_entry() -> None:
    assert set(TRANSITIONS) == set(S)


@pytest.mark.parametrize(("current", "target"), list(itertools.product(S, S)))
def test_exactly_the_agreed_edges_are_allowed(current: S, target: S) -> None:
    if (current, target) in ALLOWED:
        check_transition(current, target)
    else:
        with pytest.raises(InvalidTransition) as info:
            check_transition(current, target)
        assert info.value.current == current and info.value.target == target
        assert f"from {current} to {target}" in str(info.value)


def test_final_states_and_special_rules() -> None:
    assert {S.ISSUES_SENT, S.LIVE} == FINAL
    assert {(S.UNDER_REVIEW, S.MAPPED)} == NEEDS_REASON
    assert {
        (S.READY_FOR_SIGNOFF, t) for t in (S.LIVE, S.UNDER_REVIEW, S.ISSUES_SENT)
    } == SIGNOFF_ONLY
    assert {S.UNDER_REVIEW, S.ISSUES_SENT} == REJECTION_TARGETS
    with pytest.raises(InvalidTransition, match=r"allowed: none \(final status\)"):
        check_transition(S.ISSUES_SENT, S.RECEIVED)


def test_invalid_transition_is_an_http_409_conflict() -> None:
    from ai_platform_auth import Conflict

    assert issubclass(InvalidTransition, Conflict)
