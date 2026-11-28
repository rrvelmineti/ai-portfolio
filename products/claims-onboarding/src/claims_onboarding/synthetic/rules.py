"""Rule checks with the original claims app's semantics (``claims_onboarding/rules.py`` in
``claims-onboarding-platform``), so the ported 2,000-row sample keeps its exact 52 errors.

Semantics, per row:

- a blank value fails only ``required`` (if the field has it); other rules skip blanks;
- single-value rules (format, number, ICD-10, lengths, allowed values) run on non-blanks;
- ``not in the future`` and cross-field rules (``not before X``, ``not greater than X``)
  run only when the values involved parse; a value that failed its own format rule is
  never reported again by them;
- ``unique`` reports later repeats (case-insensitive), never the first occurrence.

Client profiles add two things the old engine didn't have: the client's own date format
(``date MM/DD/YYYY`` etc.) and an amount style (``plain`` or ``currency``, which also
accepts ``$`` and thousands separators).

Phase 6 ports the production rules engine; it must agree with these checks on every
synthetic file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from claims_onboarding.synthetic.dictionary import Dictionary, RuleSpec

PLAIN_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)")
CURRENCY_NUMBER = re.compile(r"-?\$?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")
# ICD-10-CM shape: a letter (not U), a digit, an alphanumeric, then an optional dot and 1-4 more.
ICD10 = re.compile(r"[A-TV-Z][0-9][0-9A-Z](?:\.?[0-9A-Z]{1,4})?")
DATE_FORMATS: dict[str, tuple[str, str]] = {
    "YYYY-MM-DD": ("%Y-%m-%d", r"[0-9]{4}-[0-9]{2}-[0-9]{2}"),
    "DD/MM/YYYY": ("%d/%m/%Y", r"[0-9]{2}/[0-9]{2}/[0-9]{4}"),
    "MM/DD/YYYY": ("%m/%d/%Y", r"[0-9]{2}/[0-9]{2}/[0-9]{4}"),
    "DD-MM-YYYY": ("%d-%m-%Y", r"[0-9]{2}-[0-9]{2}-[0-9]{4}"),
    "MM-DD-YYYY": ("%m-%d-%Y", r"[0-9]{2}-[0-9]{2}-[0-9]{4}"),
    "YYYY/MM/DD": ("%Y/%m/%d", r"[0-9]{4}/[0-9]{2}/[0-9]{2}"),
}
AMOUNT_STYLES = ("plain", "currency")
_DEFERRED = ("required", "unique", "not in the future", "not before ", "not greater than ")


@dataclass(frozen=True, order=True)
class Finding:
    row: int  # 1-based data row
    field: str
    rule: str
    severity: str


def parse_date(value: str, label: str) -> date | None:
    fmt, pattern = DATE_FORMATS[label]
    if not re.fullmatch(pattern, value):
        return None
    try:
        return datetime.strptime(value, fmt).date()
    except ValueError:
        return None


def format_date(value: date, label: str) -> str:
    return value.strftime(DATE_FORMATS[label][0])


def parse_amount(value: str, style: str) -> Decimal | None:
    pattern = CURRENCY_NUMBER if style == "currency" else PLAIN_NUMBER
    if not pattern.fullmatch(value):
        return None
    try:
        return Decimal(value.replace("$", "").replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the patterns only admit valid decimals
        return None


def _fails(rule: str, value: str, amount_style: str) -> bool:
    """A single-value rule on a non-blank value: True if the value breaks it."""
    if rule == "number":
        return parse_amount(value, amount_style) is None
    if rule == "not negative":
        amount = parse_amount(value, amount_style)
        return amount is not None and amount < 0
    if m := re.fullmatch(r"exactly (\d+) digits?", rule):
        return re.fullmatch(rf"[0-9]{{{m.group(1)}}}", value) is None
    if m := re.fullmatch(r"exactly (\d+) characters?", rule):
        return len(value) != int(m.group(1))
    if m := re.fullmatch(r"date (\S+)", rule):
        return parse_date(value, m.group(1)) is None
    if rule == "ICD-10 format":
        return ICD10.fullmatch(value.upper()) is None
    if m := re.fullmatch(r"one of (.+)", rule):
        allowed = {o.strip().casefold() for o in m.group(1).split("/") if o.strip()}
        return value.casefold() not in allowed
    raise ValueError(f"unknown rule: {rule!r}")


def _typed(rules: Sequence[RuleSpec], value: str, amount_style: str) -> date | Decimal | None:
    for r in rules:
        if r.text.startswith("date "):
            return parse_date(value, r.text.removeprefix("date "))
        if r.text == "number":
            return parse_amount(value, amount_style)
    return None


def check_rows(
    rows: Sequence[Mapping[str, str]],
    dictionary: Dictionary,
    *,
    amount_style: str,
    as_of: date,
) -> list[Finding]:
    """Every rule failure in ``rows`` (dicts of target field -> raw value)."""
    findings: list[Finding] = []
    seen: dict[str, set[str]] = {f.name: set() for f in dictionary.fields}
    for index, raw in enumerate(rows, start=1):
        values = {f.name: raw[f.name].strip() for f in dictionary.fields if f.name in raw}
        typed: dict[str, date | Decimal | None] = {}
        for spec in dictionary.fields:
            if spec.name not in values:
                continue
            value = values[spec.name]

            def add(rule: RuleSpec, field: str = spec.name, row: int = index) -> None:
                findings.append(Finding(row, field, rule.text, rule.severity))

            by_text = {r.text: r for r in spec.rules}
            if not value:
                if "required" in by_text:
                    add(by_text["required"])
                continue
            for rule in spec.rules:
                if not rule.text.startswith(_DEFERRED) and _fails(rule.text, value, amount_style):
                    add(rule)
            typed[spec.name] = _typed(spec.rules, value, amount_style)
            future = by_text.get("not in the future")
            if future and isinstance(typed[spec.name], date) and typed[spec.name] > as_of:  # type: ignore[operator]
                add(future)
            if "unique" in by_text:
                key = value.casefold()
                if key in seen[spec.name]:
                    add(by_text["unique"])
                seen[spec.name].add(key)
        for name, value_typed in typed.items():
            for rule in dictionary.field(name).rules:
                m = re.fullmatch(r"not (before|greater than) ([a-z0-9_]+)", rule.text)
                if not m:
                    continue
                other = typed.get(m.group(2))
                if value_typed is None or other is None:
                    continue
                if (m.group(1) == "before" and value_typed < other) or (  # type: ignore[operator]
                    m.group(1) == "greater than" and value_typed > other  # type: ignore[operator]
                ):
                    findings.append(Finding(index, name, rule.text, rule.severity))
    return sorted(findings)
