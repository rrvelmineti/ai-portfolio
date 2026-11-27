"""Guardrails-v2 PII fixes: obfuscated emails, whole addresses, written-out dates of birth
and IDs written with separators. All values are invented for these tests (synthetic)."""

from __future__ import annotations

import pytest
from ai_platform_guardrails.pii import mask_text
from ai_platform_guardrails.pii.config import GuardrailsPiiConfig
from ai_platform_guardrails.pii.engine import Detection, _merge_addresses, detect


def found(text: str) -> list[tuple[str, str]]:
    return [(d.entity, text[d.start : d.end]) for d in detect(text, GuardrailsPiiConfig())]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Reach me at lena dot hart at example dot net today", "lena dot hart at example dot net"),
        ("contact: o.reyes [at] example [dot] org", "o.reyes [at] example [dot] org"),
        ("email omar{at}example{dot}co{dot}uk please", "omar{at}example{dot}co{dot}uk"),
        (
            "write to t_ng (at) mail (dot) example (dot) com",
            "t_ng (at) mail (dot) example (dot) com",
        ),
    ],
)
def test_obfuscated_emails_are_masked_as_email(text: str, value: str) -> None:
    assert ("EMAIL", value) in found(text)
    assert value not in mask_text(text, reversible=False).masked_text


@pytest.mark.parametrize(
    "text",
    [
        "Look at the dot com era charts.",
        "We met at home dot com was not a thing then.",
        "Plain email bo@example.com is handled by the default recognizer.",
    ],
)
def test_obfuscated_email_recognizer_does_not_fire_on_prose(text: str) -> None:
    obfuscated = [v for e, v in found(text) if e == "EMAIL" and "@" not in v]
    assert obfuscated == []


@pytest.mark.parametrize(
    ("text", "address"),
    [
        ("Ship to RR 4 Box 22, Lamar, CO 81052 by Friday.", "RR 4 Box 22, Lamar, CO 81052"),
        ("Rural Route 7 Box 310, Ely, MN 55731", "Rural Route 7 Box 310, Ely, MN 55731"),
        ("Use HC 61 Box 9, Alpine, TX 79830 for mail.", "HC 61 Box 9, Alpine, TX 79830"),
        ("Remit to POB 1188 only.", "POB 1188"),
        ("Post Office Box 42, Tupelo, MS 38802", "Post Office Box 42, Tupelo, MS 38802"),
        ("Unit 2050, Box 410, DPO AP 96522", "Unit 2050, Box 410, DPO AP 96522"),
        (
            "Lives at 9 Quarry Rd., Ste 4, Barre, VT 05641 now.",
            "9 Quarry Rd., Ste 4, Barre, VT 05641",
        ),
        ("Moved to 31 Pine Ct Bldg C, Moab UT 84532.", "31 Pine Ct Bldg C, Moab UT 84532"),
        ("Deliver to 5 Oak Ave #12, Salem, OR 97301", "5 Oak Ave #12, Salem, OR 97301"),
    ],
)
def test_addresses_are_masked_whole(text: str, address: str) -> None:
    assert ("ADDRESS", address) in found(text)
    masked = mask_text(text, reversible=False).masked_text
    for part in address.replace(",", " ").split():
        if any(c.isdigit() for c in part) or part in {"Lamar", "Ely", "Alpine", "Tupelo",
                                                        "Barre", "Moab", "Salem"}:  # fmt: skip
            assert part not in masked, f"{part!r} left visible"


def test_merge_absorbs_overlapping_detections_into_the_address() -> None:
    text = "at 12 Elm St, Cody, WY 82414 today"
    street = Detection("ADDRESS", 3, 12, 0.7)
    city = Detection("PERSON", 14, 18, 0.85)
    merged = _merge_addresses(text, [street, city])
    assert [(d.entity, text[d.start : d.end]) for d in merged] == [
        ("ADDRESS", "12 Elm St, Cody, WY 82414")
    ]
    assert _merge_addresses(text, [city]) == [city]


@pytest.mark.parametrize(
    ("text", "dob"),
    [
        ("Her date of birth is the 9th of June 1955.", "the 9th of June 1955"),
        ("DOB: Sept 14th, '83 per the card", "Sept 14th, '83"),
        ("He was born the twenty-first of October, 1940.", "the twenty-first of October, 1940"),
        ("Geburtsdatum 7. Oktober 1966 laut Antrag", "7. Oktober 1966"),
        ("nacido el 2 de abril de 1977 en Lima", "2 de abril de 1977"),
    ],
)
def test_written_out_dates_of_birth_with_context(text: str, dob: str) -> None:
    assert ("DATE_OF_BIRTH", dob) in found(text)


@pytest.mark.parametrize(
    "text",
    [
        "The review is on the 9th of June 2026.",  # no DOB word: a date of service
        "Renewal letters go out Sept 14th, '27.",
    ],
)
def test_written_out_dates_without_dob_context_stay(text: str) -> None:
    assert not [e for e, _ in found(text) if e == "DATE_OF_BIRTH"]


@pytest.mark.parametrize(
    ("text", "entity", "value"),
    [
        ("member id QRS-482-119-330 on file", "MEMBER_ID", "QRS-482-119-330"),
        ("member id qrs 482 119 330 on file", "MEMBER_ID", "qrs 482 119 330"),
        ("member no. M 5521 0098 printed", "MEMBER_ID", "M 5521 0098"),
        ("policy pol-55120034 renewed", "POLICY_NUMBER", "pol-55120034"),
        ("policy POL#77003391 renewed", "POLICY_NUMBER", "POL#77003391"),
        ("claim CLM 2026 1100442 opened", "CLAIM_NUMBER", "CLM 2026 1100442"),
        ("claim clm-2026-1100443 opened", "CLAIM_NUMBER", "clm-2026-1100443"),
    ],
)
def test_ids_with_separators_and_either_case(text: str, entity: str, value: str) -> None:
    assert (entity, value) in found(text)
