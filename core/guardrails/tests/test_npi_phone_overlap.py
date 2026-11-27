"""NPI vs phone overlap.

An NPI is 10 digits starting with 1 or 2 whose Luhn check (with the 80840 prefix) passes.
The bare-NANP phone rule accepts 10 digits whose 1st and 4th digits are 2-9. So an NPI
starting with 2 (4th digit 2-9) matches both rules, and about 1 in 10 phone numbers
starting with 2 pass the NPI check.

Either way the value is masked; these tests pin which *label* wins, because a wrong label
changes what the model is told the value is (and Safe Harbor treats them differently).
"""

from __future__ import annotations

import pytest
from ai_platform_guardrails.pii.config import GuardrailsPiiConfig
from ai_platform_guardrails.pii.engine import detect
from ai_platform_guardrails.pii.masking import mask_text

NPI_2 = "2873640157"  # Luhn-valid NPI starting with 2, also NANP-shaped
NPI_2B = "2519038477"  # another one
PHONE_NOT_NPI = "2025550173"  # NANP-shaped, fails the NPI Luhn check
PHONE_ALSO_NPI = "2125550147"  # a phone number that happens to pass the NPI Luhn check


def labels(text: str) -> dict[str, str]:
    return {text[d.start : d.end]: d.entity for d in detect(text, GuardrailsPiiConfig())}


def test_npi_with_context_is_labelled_npi_not_phone() -> None:
    assert labels(f"Rendering provider NPI {NPI_2} billed for the visit.")[NPI_2] == "NPI"


def test_npi_without_context_is_labelled_npi() -> None:
    assert labels(f"Provider {NPI_2B} billed for the visit.").get(NPI_2B) == "NPI"


def test_non_luhn_number_next_to_call_is_a_phone() -> None:
    assert labels(f"Please call {PHONE_NOT_NPI} after 3pm.").get(PHONE_NOT_NPI) == "PHONE"


def test_npi_and_phone_in_one_sentence_each_get_their_label() -> None:
    text = f"Dr. Achebe (NPI {NPI_2}) can be reached on {PHONE_NOT_NPI}."
    found = labels(text)
    assert found.get(NPI_2) == "NPI"
    assert found.get(PHONE_NOT_NPI) == "PHONE"


def test_formatted_phone_that_passes_npi_luhn_stays_a_phone() -> None:
    text = "Please call 212-555-0147 after 3pm."
    assert labels(text).get("212-555-0147") == "PHONE"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known overlap, not fixed in the held-out branch (no detector changes): a bare "
        "10-digit phone that passes the NPI Luhn check is labelled NPI even next to "
        "'call'. It is still masked."
    ),
)
def test_bare_phone_that_passes_npi_luhn_next_to_call_is_a_phone() -> None:
    assert labels(f"Please call {PHONE_ALSO_NPI} after 3pm.").get(PHONE_ALSO_NPI) == "PHONE"


@pytest.mark.parametrize("value", [NPI_2, NPI_2B, PHONE_NOT_NPI, PHONE_ALSO_NPI])
@pytest.mark.parametrize("template", ["NPI {v}", "call {v}", "ref {v} on file"])
def test_overlapping_numbers_are_always_masked(template: str, value: str) -> None:
    masked = mask_text(template.format(v=value), reversible=False).masked_text
    assert value not in masked
