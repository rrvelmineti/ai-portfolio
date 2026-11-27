from __future__ import annotations

from pathlib import Path

import pytest
from ai_platform_gateway import PolicyViolation
from ai_platform_gateway.testing import FakeProvider, completion, fake_gateway
from ai_platform_guardrails.install import build_guard
from ai_platform_guardrails.policy import BlockedTopic, ProductPolicy, load_policy

POLICIES = Path(__file__).resolve().parents[1] / "policies"


def test_load_policy_files_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    rewards = load_policy(POLICIES / "rewards-advisor.yaml")
    assert (
        rewards.product == "rewards-advisor"
        and rewards.blocked_topics[0].name == "investment_advice"
    )
    assert "lookup_offer" in rewards.tool_allowlist
    monkeypatch.delenv("GUARDRAILS_POLICY", raising=False)
    assert load_policy().blocked_topics == []
    monkeypatch.setenv("GUARDRAILS_POLICY", str(POLICIES / "meal-vision.yaml"))
    assert load_policy().product == "meal-vision"


@pytest.mark.parametrize(
    ("text", "blocked"),
    [
        ("Should I buy Tesla stocks with my points cash-out?", True),
        ("Can you give me investment advice?", True),
        ("Which card earns the most points on groceries?", False),
        ("How do I invest points into a travel upgrade?", False),
        ("Is the points programme a good investment of my time?", False),
    ],
)
def test_rewards_topic_matching(text: str, blocked: bool) -> None:
    policy = load_policy(POLICIES / "rewards-advisor.yaml")
    assert (policy.match(text, "input") is not None) is blocked


def test_meal_vision_topics() -> None:
    policy = load_policy(POLICIES / "meal-vision.yaml")
    assert policy.match("How much insulin should I take for this pasta?", "input")
    assert policy.match("Should I stop taking my medication before dinner?", "input")
    assert policy.match("How many calories are in this salad?", "input") is None


def test_policy_blocks_input_and_output_with_refusal() -> None:
    policy = ProductPolicy(product="t", blocked_topics=[
        BlockedTopic(name="medical_advice", refusal="Please ask a doctor.",
                     phrases=["medical advice"]),
        BlockedTopic(name="legal", refusal="No legal advice.", applies_to="output",
                     phrases=["you should sue"]),
    ])  # fmt: skip
    p = FakeProvider("anthropic", [completion("Honestly, you should sue them.")])
    gw = fake_gateway({"anthropic": p}, guard=build_guard(policy=policy))
    with pytest.raises(PolicyViolation) as info:
        gw.complete("I need medical advice about my rash")
    assert info.value.topic == "medical_advice" and info.value.refusal == "Please ask a doctor."
    assert p.requests == []  # blocked before the provider
    with pytest.raises(PolicyViolation) as out:
        gw.complete("My landlord kept the deposit, what now?")  # input side doesn't apply
    assert out.value.topic == "legal" and len(p.requests) == 1


def test_policy_link_allowlist_used_by_guard() -> None:
    policy = ProductPolicy(link_allowlist=["sec.gov"])
    guard = build_guard(policy=policy)
    assert guard.policy is policy
    p = FakeProvider("anthropic", [completion("See [filing](https://www.sec.gov/x)")])
    assert fake_gateway({"anthropic": p}, guard=guard).complete("Where?").output_flags == []
