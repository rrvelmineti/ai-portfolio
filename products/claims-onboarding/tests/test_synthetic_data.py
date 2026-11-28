"""The committed synthetic extracts: deterministic, checked both ways, varied, synthetic."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from claims_onboarding.synthetic.check import check_all, check_file
from claims_onboarding.synthetic.dictionary import load_dictionary
from claims_onboarding.synthetic.formats import FileFormat, read, write
from claims_onboarding.synthetic.generate import (
    SYNTHETIC_DIR,
    generate_all,
    npi_check_digit,
    npi_is_valid,
    port_legacy,
)
from claims_onboarding.synthetic.profiles import CLIENTS
from claims_onboarding.synthetic.rules import check_rows, parse_amount, parse_date

MANIFEST: dict[str, Any] = json.loads((SYNTHETIC_DIR / "manifest.json").read_text("utf-8"))
ENTRIES = MANIFEST["files"]


def _entry_id(entry: dict[str, Any]) -> str:
    return f"{entry['code']}-week{entry['week']}"


def _load(
    entry: dict[str, Any], root: Path = SYNTHETIC_DIR
) -> tuple[Any, Any, list[dict[str, str]]]:
    client = next(c for c in CLIENTS if c.number == entry["client"])
    headers, rows = read(client.fmt, (root / entry["path"]).read_bytes())
    mapping = json.loads((root / entry["mapping_key"]["path"]).read_text("utf-8"))["columns"]
    errors = json.loads((root / entry["error_key"]["path"]).read_text("utf-8"))["errors"]
    records = [dict(zip(headers, r, strict=True)) for r in rows]
    return mapping, errors, records


# ---------------------------------------------------------------- determinism and checker


def test_generator_is_deterministic(tmp_path: Path) -> None:
    """Same seed, same bytes: regenerating reproduces every committed file and the manifest."""
    generate_all(tmp_path)
    committed = sorted(
        p.relative_to(SYNTHETIC_DIR)
        for p in SYNTHETIC_DIR.rglob("*")
        if p.is_file() and p.name != "README.md"
    )
    generated = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert generated == committed
    different = [
        p for p in committed if (tmp_path / p).read_bytes() != (SYNTHETIC_DIR / p).read_bytes()
    ]
    assert different == []


def test_checker_passes_on_every_file() -> None:
    assert len(ENTRIES) == 20
    assert {e["client"] for e in ENTRIES} == set(range(1, 11))
    assert check_all() == []


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic"
    shutil.copytree(SYNTHETIC_DIR, root)
    return root


def test_checker_catches_a_new_error_in_a_file(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    entry = next(e for e in ENTRIES if e["code"] == "LSM" and e["week"] == 1)
    path = root / entry["path"]
    lines = path.read_bytes().decode("utf-8").split("\n")
    cells = lines[5].split("\t")  # data row 4, which has no planted error
    cells[0] = ""  # claim_id is required
    lines[5] = "\t".join(cells)
    path.write_bytes("\n".join(lines).encode("utf-8"))
    problems = check_file(entry, root)
    assert any("bytes don't match the manifest" in p for p in problems)
    assert any(
        "errors not in the key" in p and "row=5, field='claim_id', rule='required'" in p
        for p in problems
    )


def test_checker_catches_a_key_that_lies(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    entry = next(e for e in ENTRIES if e["code"] == "PCH" and e["week"] == 2)
    key_path = root / entry["error_key"]["path"]
    key = json.loads(key_path.read_text("utf-8"))
    dropped = key["errors"].pop(0)
    key["errors"].append(
        {"row": 1, "line": 2, "field": "member_id", "rule": "required", "severity": "blocker"}
    )
    key_path.write_text(json.dumps(key), encoding="utf-8")
    problems = check_file(entry, root)
    assert any("error_key doesn't match the manifest" in p for p in problems)
    assert any(
        "keyed errors the checks don't find" in p and "field='member_id'" in p for p in problems
    )
    assert any(
        "errors not in the key" in p and f"field={dropped['field']!r}" in p for p in problems
    )


def test_checker_catches_bad_mapping_keys(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    entry = next(e for e in ENTRIES if e["code"] == "GPH" and e["week"] == 1)
    key_path = root / entry["mapping_key"]["path"]
    key = json.loads(key_path.read_text("utf-8"))
    by_header = {c["header"]: c for c in key["columns"]}
    by_header["SVC_THRU_DT"]["target"] = "service_date"  # the ambiguous pair, mapped twice
    by_header["CLMID"]["target"] = "unmapped"  # a required field left unmapped
    key_path.write_text(json.dumps(key), encoding="utf-8")
    problems = check_file(entry, root)
    assert any("a target is mapped twice" in p for p in problems)
    assert any("required fields not mapped: ['claim_id']" in p for p in problems)


# ---------------------------------------------------------------- client 1 baseline


def test_baseline_matches_the_original_52_error_key_exactly() -> None:
    folder = SYNTHETIC_DIR / "client_01_nwh"
    legacy = json.loads((folder / "legacy_answer_key.json").read_text("utf-8"))
    errors = json.loads((folder / "week1.errors.json").read_text("utf-8"))["errors"]
    ours = {(e["line"], e["field"], e["rule"]) for e in errors if e["origin"] == "legacy"}
    theirs = {(e["line"], e["field"], e["rule"]) for e in legacy["planted_errors"]}
    assert len(theirs) == 52 and ours == theirs
    derived = [e for e in errors if e["origin"] != "legacy"]
    assert len(derived) == 4
    assert {(e["origin"], e["field"], e["rule"]) for e in derived} == {
        ("derived_paid_date", "paid_date", "not before service_date")
    }
    # with the original 12 fields (no paid_date), the checks find exactly the 52
    entry = next(e for e in ENTRIES if e["legacy"])
    _, _, records = _load(entry)
    old_fields = set(legacy["mapping"])
    data = [{t: r[legacy["mapping"][t]] for t in old_fields} for r in records]
    found = check_rows(
        data,
        load_dictionary().for_date_format("YYYY-MM-DD", only=old_fields),
        amount_style="plain",
        as_of=date(2026, 11, 15),
    )
    assert {(f.row + 1, f.field, f.rule) for f in found} == theirs


def test_port_changes_only_procedure_codes() -> None:
    client = CLIENTS[0]
    header = ",".join(h for h, _ in client.columns).encode() + b"\n"
    fixed = "G1,M{n},1970-01-01,1111111111,2222222222,2025-01-02,2025-01-20,E11.9"
    tail = "11,10.00,9.00,8.00,paid,1"
    original = (
        header
        + "".join(
            f"C{n},{fixed.format(n=n)},{code},{tail}\n"
            for n, code in ((1, "55555"), (2, "4444A"), (3, "5555"))
        ).encode()
    )
    key = {
        "planted_errors": [{"line": 4, "field": "procedure_code", "rule": "exactly 5 characters"}]
    }
    data, mapping, errors = port_legacy(original, key, client)
    assert len(data) == len(original)
    changed = [
        (a, b) for a, b in zip(original.split(b","), data.split(b","), strict=True) if a != b
    ]
    assert changed == [(b"55555", b"Z0002"), (b"4444A", b"Z0001"), (b"5555", b"Z000")]
    assert [m["target"] for m in mapping][:3] == ["claim_id", "unmapped", "member_id"]
    assert errors == [
        {
            "row": 3,
            "line": 4,
            "field": "procedure_code",
            "rule": "exactly 5 characters",
            "severity": "blocker",
            "origin": "legacy",
        }
    ]
    with pytest.raises(ValueError, match="no quoted fields"):
        port_legacy(original.replace(b"C1", b'"C1"'), key, client)
    with pytest.raises(ValueError, match="unexpected procedure code"):
        port_legacy(original.replace(b"5555,", b"9999,"), key, client)


# ---------------------------------------------------------------- variety


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


@pytest.mark.parametrize("entry", ENTRIES, ids=_entry_id)
def test_each_file_differs_from_the_dictionary_and_maps_every_header(entry: dict[str, Any]) -> None:
    mapping, errors, _records = _load(entry)
    mapped = [c for c in mapping if c["target"] != "unmapped"]
    differences = [c for c in mapped if _norm(c["header"]) != c["target"]]
    assert len(differences) >= 3
    assert len(mapping) - len(mapped) >= 2  # extra unmapped columns
    assert 5 <= len(errors) <= 60 or entry["legacy"]


def test_the_formats_and_styles_cover_the_brief() -> None:
    formats = [e["format"] for e in ENTRIES]
    assert {f["delimiter"] for f in formats if f["kind"] == "csv"} == {",", "|", "\t"}
    assert any(f["kind"] == "xlsx" for f in formats)
    assert any(f["encoding"] == "latin-1" for f in formats)
    assert any(f["bom"] for f in formats)
    assert any(f["header_line"] > 1 and f["footer_lines"] == 1 for f in formats)
    assert len({e["date_format"] for e in ENTRIES}) >= 5
    assert {e["amount_style"] for e in ENTRIES} == {"plain", "currency"}
    rules = {e["rule"] for entry in ENTRIES for e in _load(entry)[1]}
    for wanted in (
        "required",
        "unique",
        "not in the future",
        "not before service_date",
        "not before member_dob",
        "ICD-10 format",
        "number",
        "not negative",
        "not greater than billed_amount",
        "one of paid / denied / pending / adjusted",
        "exactly 5 characters",
        "exactly 10 digits",
        "exactly 2 digits",
    ):
        assert wanted in rules
    assert any(r.startswith("date ") for r in rules)
    gph = _load(next(e for e in ENTRIES if e["code"] == "GPH"))[0]
    assert {c["header"]: c["target"] for c in gph}["SVC_THRU_DT"] == "unmapped"


@pytest.mark.parametrize("client", CLIENTS, ids=lambda c: c.code)
def test_week2_renames_one_header_adds_one_column_and_changes_the_error_mix(client: Any) -> None:
    w1, w2 = (
        next(e for e in ENTRIES if e["client"] == client.number and e["week"] == w) for w in (1, 2)
    )
    m1, e1, _ = _load(w1)
    m2, e2, _ = _load(w2)
    h1, h2 = [c["header"] for c in m1], [c["header"] for c in m2]
    assert len(h2) == len(h1) + 1
    renamed = [(a, b) for a, b in zip(h1, h2, strict=False) if a != b]
    assert len(renamed) == 1 and renamed[0] == client.week2_rename
    assert h2[-1] == client.week2_new_column[0]
    t1 = {c["header"]: c["target"] for c in m1}
    t2 = {c["header"]: c["target"] for c in m2}
    assert t2[renamed[0][1]] == t1[renamed[0][0]]  # same field, new name
    assert {(e["field"], e["rule"]) for e in e1} != {(e["field"], e["rule"]) for e in e2}


# ---------------------------------------------------------------- codes and copyright


@pytest.mark.parametrize("entry", ENTRIES, ids=_entry_id)
def test_procedure_codes_are_synthetic_z_codes(entry: dict[str, Any]) -> None:
    mapping, errors, records = _load(entry)
    header = next(c["header"] for c in mapping if c["target"] == "procedure_code")
    bad_length = {
        e["row"]
        for e in errors
        if e["field"] == "procedure_code" and e["rule"] == "exactly 5 characters"
    }
    for row, record in enumerate(records, start=1):
        value = record[header]
        assert re.fullmatch(r"Z\d{4}", value) or (row in bad_length and value.startswith("Z")), (
            row,
            value,
        )


@pytest.mark.parametrize("entry", [e for e in ENTRIES if not e["legacy"]], ids=_entry_id)
def test_npis_are_synthetic_and_luhn_valid(entry: dict[str, Any]) -> None:
    mapping, errors, records = _load(entry)
    planted = {e["row"] for e in errors if e["field"] == "provider_npi"}
    rendering = next(c["header"] for c in mapping if c["target"] == "provider_npi")
    billing = [
        c["header"]
        for c in mapping
        if c["header"] in ("BILL_NPI", "Billing Prov NPI", "Billing Provider National ID")
    ]
    for row, record in enumerate(records, start=1):
        if row not in planted:
            assert npi_is_valid(record[rendering]), (row, record[rendering])
        for h in billing:
            assert npi_is_valid(record[h])


def test_npi_check_digit_matches_the_cms_example() -> None:
    assert npi_check_digit("123456789") == "3"  # CMS: 1234567893 is a valid NPI
    assert npi_is_valid("1234567893") and not npi_is_valid("1234567890")
    assert not npi_is_valid("123456789")


def test_no_procedure_descriptions_or_cpt_labels_in_the_data() -> None:
    """No CPT labels, and no column that could carry a procedure description."""
    for entry in ENTRIES:
        mapping, _errors, _records = _load(entry)
        for column in mapping:
            header = column["header"].lower()
            assert "cpt" not in header
            assert not ("proc" in header and "desc" in header), column


# ---------------------------------------------------------------- formats and rules details


def test_formats_round_trip_and_refuse_malformed_files() -> None:
    headers, rows = ["a", "b"], [["1", ""], ["Ñ", "x"]]
    for fmt in (
        FileFormat(),
        FileFormat(delimiter="\t", encoding="latin-1"),
        FileFormat(bom=True, preamble=("Title",), footer_total=True),
        FileFormat(kind="xlsx", preamble=("Title",)),
    ):
        footer = ["TOTAL", "2"] if fmt.footer_total else None
        assert read(fmt, write(fmt, headers, rows, footer)) == (headers, rows)
    with pytest.raises(ValueError, match="byte-order mark"):
        read(FileFormat(bom=True), write(FileFormat(), headers, rows))
    with pytest.raises(ValueError, match="TOTAL footer"):
        read(FileFormat(footer_total=True), write(FileFormat(), headers, rows))
    assert FileFormat(preamble=("x", "y")).describe()["header_line"] == 3


def test_rule_parsing_details() -> None:
    assert parse_amount("$1,234.50", "currency") == parse_amount("1234.50", "currency")
    assert parse_amount("-$15.00", "currency") is not None
    assert parse_amount("$12", "plain") is None and parse_amount("1,234", "plain") is None
    assert parse_amount("12O.50", "currency") is None
    assert parse_date("02/30/2025", "MM/DD/YYYY") is None
    assert parse_date("2025-03-04", "DD/MM/YYYY") is None
    assert parse_date("04/03/2025", "DD/MM/YYYY") == date(2025, 3, 4)
    with pytest.raises(ValueError, match="unknown rule"):
        check_rows(
            [{"claim_id": "1"}],
            _dictionary_with("claim_id", "sparkles"),
            amount_style="plain",
            as_of=date(2026, 1, 1),
        )


def _dictionary_with(field: str, rule: str) -> Any:
    from claims_onboarding.synthetic.dictionary import Dictionary, FieldSpec, RuleSpec

    return Dictionary(
        "t", 1, (FieldSpec(field, "", "string", False, (RuleSpec(rule, "blocker"),)),)
    )


def test_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from claims_onboarding.synthetic import __main__ as cli
    from claims_onboarding.synthetic import check, generate, seed

    assert cli.main(["check"]) == 0
    assert "all files match" in capsys.readouterr().out
    monkeypatch.setattr(check, "check_all", lambda: ["a problem"])
    assert cli.main(["check"]) == 1
    assert "a problem" in capsys.readouterr().err
    monkeypatch.setattr(generate, "generate_all", lambda: {"files": [1, 2]})
    assert cli.main(["generate"]) == 0
    assert "wrote 2 files" in capsys.readouterr().out
    monkeypatch.setattr(seed, "main", lambda: 0)
    assert cli.main(["seed"]) == 0


def test_seed_and_check_run_without_dev_dependencies() -> None:
    """Deploy and the nightly reset install without dev tools (no Faker): seeding must work."""
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['faker'] = None\n"
        "import claims_onboarding.synthetic.seed, claims_onboarding.synthetic.check"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603 - fixed arguments
