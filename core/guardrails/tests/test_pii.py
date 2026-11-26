"""PII masking with the real Presidio model (synthetic values only)."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import pytest
from ai_platform_common.context import request_context, set_user
from ai_platform_common.logging import JsonFormatter
from ai_platform_gateway.errors import GuardrailError
from ai_platform_guardrails.pii import (
    MappingExpired,
    MaskSession,
    drop_mapping,
    mask_records,
    mask_text,
    privacy_report,
    unmask,
)
from ai_platform_guardrails.pii import engine as pii_engine
from ai_platform_guardrails.pii.config import (
    GuardrailsPiiConfig,
    PiiSettings,
    TenantConfig,
    load_pii_config,
)
from ai_platform_guardrails.pii.masking import set_config
from ai_platform_guardrails.pii.recognizers import luhn_valid, npi_valid
from ai_platform_guardrails.pii.records import header_entity

TEXT = (
    "Patient Will Grant (DOB 03/14/1961) lives at 1458 Maple Avenue Apt 2B, Springfield, "
    "IL 62704. SSN 536-22-8471, phone (217) 555-0143, email will.grant@example.com, "
    "card 4111 1111 1111 1111, IP 10.24.3.199. Member ID XGH123456789, claim "
    "CLM-2024-0012345, policy POL-00123456, NPI 1245319599. Will Grant called again."
)
VALUES = ["Will Grant", "03/14/1961", "1458 Maple Avenue", "536-22-8471", "(217) 555-0143",
          "will.grant@example.com", "4111 1111 1111 1111", "10.24.3.199", "XGH123456789",
          "CLM-2024-0012345", "POL-00123456", "1245319599"]  # fmt: skip


def test_all_entity_types_masked_with_typed_placeholders() -> None:
    with request_context("r-pii"):
        res = mask_text(TEXT)
    for value in VALUES:
        assert value not in res.masked_text, value
    for ph in ("<PERSON_1>", "<DATE_OF_BIRTH_1>", "<ADDRESS_1>", "<US_SSN_1>", "<PHONE_1>",
               "<EMAIL_1>", "<CREDIT_CARD_1>", "<IP_ADDRESS_1>", "<MEMBER_ID_1>",
               "<CLAIM_NUMBER_1>", "<POLICY_NUMBER_1>", "<NPI_1>"):  # fmt: skip
        assert ph in res.masked_text, ph
    first = res.entities[0]
    assert TEXT[first.start : first.end] == "Will Grant" and first.type == "PERSON"
    assert not hasattr(first, "value")


def test_same_value_same_placeholder_within_a_session() -> None:
    with request_context("r-consistent"):
        res = mask_text("Dear Ana Ruiz, Ana Ruiz's claim was paid. Contact Ben Ode about it.")
        assert res.masked_text.count("<PERSON_1>") == 2 and "<PERSON_2>" in res.masked_text
        session = MaskSession()
        a = session.mask("Member Ana Ruiz called.")
        b = session.mask("Then Member Ana Ruiz wrote, and Member Ben Ode replied.")
        assert "<PERSON_1>" in a.masked_text and b.masked_text.count("<PERSON_1>") == 1
        assert "<PERSON_2>" in b.masked_text and a.mapping_id == b.mapping_id


def test_unmask_round_trip_and_mapping_dropped_at_request_end() -> None:
    with request_context("r-unmask") as ctx:
        res = mask_text(TEXT)
        assert res.mapping_id is not None
        assert unmask(res.masked_text, res.mapping_id) == TEXT
        letter = "Dear <PERSON_1>, your claim <CLAIM_NUMBER_1> is approved."
        assert unmask(letter, res.mapping_id) == (
            "Dear Will Grant, your claim CLM-2024-0012345 is approved."
        )
        assert "Will Grant" not in repr(ctx) and "Will Grant" not in repr(res)
    with pytest.raises(MappingExpired):
        unmask(res.masked_text, res.mapping_id)  # request over: mapping gone
    with request_context("r-other"), pytest.raises(MappingExpired):
        unmask(res.masked_text, res.mapping_id)  # another request can't see it
    with request_context("r-drop"):
        r2 = mask_text("Member Ana Ruiz")
        assert r2.mapping_id is not None
        drop_mapping(r2.mapping_id)
        with pytest.raises(MappingExpired):
            unmask(r2.masked_text, r2.mapping_id)
    # outside any request nothing is stored
    r3 = mask_text("Member Ana Ruiz")
    with pytest.raises(MappingExpired):
        unmask(r3.masked_text, r3.mapping_id or "")
    assert mask_text("Member Ana Ruiz", reversible=False).mapping_id is None


def test_mapping_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG), request_context("r-log"):
        res = mask_text(TEXT)
        unmask(res.masked_text, res.mapping_id or "")
        logging.getLogger("t").info("after", extra={"fields": {"mask": repr(res)}})
    out = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for value in VALUES:
        assert value not in out


def test_fail_closed_when_presidio_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("spaCy exploded")

    monkeypatch.setattr(pii_engine, "get_analyzer", boom)
    with pytest.raises(GuardrailError, match="fail closed"):
        mask_text("Member Ana Ruiz")
    with pytest.raises(GuardrailError):
        mask_records([{"name": "Ana Ruiz"}])


def test_safe_harbor_generalises_dates_zip_and_age() -> None:
    text = (
        "Member Ana Ruiz, DOB 1931-02-11, age 93, seen 2024-05-02 at ZIP 62704 and "
        "again on March 3, 2024 in IL 03601. A 45-year-old sibling attended Monday."
    )
    with request_context("r-harbor"):
        plain = mask_text(text).masked_text
        harbor = mask_text(text, safe_harbor=True).masked_text
    assert "2024-05-02" in plain and "62704" in plain  # off by default
    assert "<DATE_OF_BIRTH_1>" in harbor  # DOB stays fully masked
    assert "2024-05-02" not in harbor and "March 3" not in harbor and "2024" in harbor
    assert "62704" not in harbor and "627**" in harbor
    assert "000**" in harbor  # restricted 3-digit ZIP
    assert "age 90+" in harbor and "93" not in harbor
    assert "45-year-old" in harbor and "Monday" in harbor


def test_location_off_by_default_and_switchable() -> None:
    text = "She moved from Springfield to Ohio last year."
    assert mask_text(text).masked_text == text
    cfg = GuardrailsPiiConfig(pii=PiiSettings(mask_location=True))
    masked = mask_text(text, config=cfg).masked_text
    assert "<LOCATION_1>" in masked and "Ohio" not in masked


def test_tenant_custom_recognizer(tmp_path: Path) -> None:
    tid = str(uuid.uuid4())
    path = tmp_path / "guardrails.yaml"
    path.write_text(
        "pii:\n  mask_location: false\n"
        f"tenants:\n  '{tid}':\n    recognizers:\n"
        "      - entity: MEMBER_ID\n"
        "        patterns: [{name: acme, regex: '\\bACM-\\d{7}\\b', score: 0.4}]\n"
        "        context: [acme id, member]\n"
    )
    cfg = load_pii_config(path)
    assert isinstance(cfg.tenants[tid], TenantConfig)
    set_config(cfg)
    text = "The member's Acme ID ACM-1234567 is on file."
    assert "ACM-1234567" in mask_text(text).masked_text  # other tenants: not recognised
    with request_context("r-tenant"):
        set_user(uuid.UUID(tid), uuid.uuid4())
        assert "<MEMBER_ID_1>" in mask_text(text).masked_text  # tenant from the token
    assert "<MEMBER_ID_1>" in mask_text(text, tenant_id=tid).masked_text
    assert load_pii_config(None).pii.hipaa_safe_harbor is False


def test_npi_and_luhn() -> None:
    assert luhn_valid("4111111111111111") and not luhn_valid("4111111111111112")
    assert npi_valid("1245319599") and not npi_valid("1245319598") and not npi_valid("12453")
    assert "<NPI_1>" in mask_text("Rendering NPI 1245319599").masked_text
    assert "1245319598" in mask_text("Rendering NPI 1245319598").masked_text  # bad check digit


def test_dates_that_are_not_dob_stay() -> None:
    res = mask_text("Date of service 05/02/2024; admitted 2024-04-30; DOB 01/09/1980.")
    assert "05/02/2024" in res.masked_text and "2024-04-30" in res.masked_text
    assert "01/09/1980" not in res.masked_text


def test_mask_records_columns_consistency_and_report() -> None:
    rows = [
        {"Member Name": "Will Grant", "Member_ID": "XGH123456789", "DOB": "1961-03-14",
         "Zip": "62704", "Notes": "Spoke to Dr. Ana Ruiz about CLM-2024-0012345", "Paid": "120.50",
         "Date of Service": "2024-05-02"},
        {"Member Name": "Will Grant", "Member_ID": "XGH123456789", "DOB": "1961-03-14",
         "Zip": "03601", "Notes": "", "Paid": "80", "Date of Service": "2024-06-11"},
    ]  # fmt: skip
    with request_context("r-records"):
        res = mask_records(rows)
        assert res.column_types["Member Name"] == "PERSON"
        assert res.column_types["Member_ID"] == "MEMBER_ID"
        assert res.column_types["DOB"] == "DATE_OF_BIRTH"
        assert res.column_types["Paid"] is None
        assert res.rows[0]["Member Name"] == res.rows[1]["Member Name"] == "<PERSON_1>"
        assert res.rows[0]["Zip"] == "62704"  # ZIP only generalised in Safe Harbor mode
        assert "Ana Ruiz" not in res.rows[0]["Notes"] and "<CLAIM_NUMBER_1>" in res.rows[0]["Notes"]
        assert unmask(res.rows[0]["Member Name"], res.mapping_id or "") == "Will Grant"
        harbor = mask_records(rows, safe_harbor=True)
        assert harbor.rows[0]["Zip"] == "627**" and harbor.rows[1]["Zip"] == "000**"
        assert harbor.rows[0]["Date of Service"] == "2024"
        only = mask_records(rows, columns=["Member_ID"])
        assert only.rows[0]["Member Name"] == "Will Grant"
        report = privacy_report(res, mask_text("Member Ana Ruiz"))
    dumped = json.dumps(report.as_dict())
    assert report.entity_counts["PERSON"] >= 3 and "Member Name" in report.flagged_columns
    assert report.rows_scanned == 2 and report.texts_scanned == 1
    for value in ("Will Grant", "XGH123456789", "Ana Ruiz", "62704"):
        assert value not in dumped and value not in repr(res)


@pytest.mark.parametrize(
    ("header", "entity"),
    [
        ("ssn", "US_SSN"), ("Member ID", "MEMBER_ID"), ("claim_no", "CLAIM_NUMBER"),
        ("Policy Number", "POLICY_NUMBER"), ("Billing NPI", "NPI"),
        ("date_of_birth", "DATE_OF_BIRTH"), ("E-mail", "EMAIL"), ("Mobile", "PHONE"),
        ("last_name", "PERSON"), ("Street Address", "ADDRESS"), ("Postal Code", "ZIP_CODE"),
        ("ip_address", "IP_ADDRESS"), ("Card Number", "CREDIT_CARD"),
        ("Bank Account", "US_BANK_NUMBER"), ("amount", None),
    ],
)  # fmt: skip
def test_header_hints(header: str, entity: str | None) -> None:
    assert header_entity(header) == entity


def test_empty_text() -> None:
    assert mask_text("   ").masked_text == "   "
