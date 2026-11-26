"""Tabular masking for CSV/Excel rows, and a values-free privacy report.

Each column is classified from its header (keyword match) plus a sample of values
(analysed with the header as context). Flagged columns are masked cell by cell with one
shared session, so the same value gets the same placeholder in every row; other text
columns are scanned as free text.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ai_platform_gateway.errors import GuardrailError

from ai_platform_guardrails.pii.config import GuardrailsPiiConfig
from ai_platform_guardrails.pii.engine import detect
from ai_platform_guardrails.pii.masking import (
    MaskResult,
    MaskSession,
    _generalise_date,
    _generalise_zip,
    get_config,
)

# Header keyword -> entity. Matched on whole words of the normalised header.
HEADER_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(ssn|social security|soc sec)\b"), "US_SSN"),
    (re.compile(r"\b(member|mbr|subscriber|enrollee)\b.*\b(id|no|number|num)\b|\bmember_?id\b"),
     "MEMBER_ID"),
    (re.compile(r"\bclaim\b.*\b(id|no|number|num)\b|\bclaim_?(id|no|number)\b|\bicn\b"),
     "CLAIM_NUMBER"),
    (re.compile(r"\bpolicy\b.*\b(id|no|number|num)\b|\bpolicy_?(id|no|number)\b"),
     "POLICY_NUMBER"),
    (re.compile(r"\bnpi\b"), "NPI"),
    (re.compile(r"\b(dob|d o b|date of birth|birth ?date|birthdate)\b"), "DATE_OF_BIRTH"),
    (re.compile(r"\b(e ?mail|email address)\b"), "EMAIL"),
    (re.compile(r"\b(phone|tel|telephone|mobile|cell|fax)\b"), "PHONE"),
    (re.compile(r"\b(name|first name|last name|surname|given name|patient|insured|claimant|"
                r"beneficiary|full name)\b"), "PERSON"),
    (re.compile(r"\b(ip|ip address)\b"), "IP_ADDRESS"),
    (re.compile(r"\b(address|street|addr|mailing address)\b"), "ADDRESS"),
    (re.compile(r"\b(zip|zip code|postal|postcode)\b"), "ZIP_CODE"),
    (re.compile(r"\b(card|credit card|cc number|card number)\b"), "CREDIT_CARD"),
    (re.compile(r"\b(account|acct|routing|bank account)\b"), "US_BANK_NUMBER"),
]  # fmt: skip
# Columns whose values are dates of service etc.: generalised only in Safe Harbor mode.
DATE_HEADER = re.compile(r"\b(date|dos|admit|discharge|service|visit|dt)\b")


def _norm_header(header: str) -> str:
    return re.sub(r"[_\-./]+", " ", header).strip().lower()


def header_entity(header: str) -> str | None:
    h = _norm_header(header)
    for rx, entity in HEADER_HINTS:
        if rx.search(h):
            return entity
    return None


@dataclass
class RecordsMaskResult:
    rows: list[dict[str, Any]]
    column_types: dict[str, str | None]
    mapping_id: str | None
    entity_counts: dict[str, int] = field(default_factory=dict)
    rows_scanned: int = 0

    @property
    def flagged_columns(self) -> list[str]:
        return [c for c, t in self.column_types.items() if t]

    def __repr__(self) -> str:
        return (f"RecordsMaskResult(rows={self.rows_scanned}, flagged={self.flagged_columns}, "
                f"entities={self.entity_counts})")  # fmt: skip


def classify_columns(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    config: GuardrailsPiiConfig,
    *,
    sample_size: int = 20,
    tenant_id: str | None = None,
) -> dict[str, str | None]:
    """Entity type per column (or None), from the header and sampled values."""
    result: dict[str, str | None] = {}
    for col in columns:
        hinted = header_entity(col)
        values = [str(r.get(col, "")).strip() for r in rows[:sample_size]]
        values = [v for v in values if v]
        if not values:
            result[col] = hinted
            continue
        votes: Counter[str] = Counter()
        for v in values:
            found = detect(f"{col}: {v}", config, tenant_id=tenant_id, extra_context=[col])
            covering = [d.entity for d in found if d.end - d.start >= max(1, len(v) // 2)]
            votes.update(set(covering))
        top, n = votes.most_common(1)[0] if votes else (None, 0)
        if hinted and (votes[hinted] > 0 or n == 0):
            result[col] = hinted
        elif top and n >= max(1, len(values) // 2):
            result[col] = top
        else:
            result[col] = hinted
    return result


def mask_records(
    rows: Iterable[Mapping[str, Any]],
    columns: Sequence[str] | None = None,
    *,
    tenant_id: str | None = None,
    safe_harbor: bool | None = None,
    reversible: bool = True,
    config: GuardrailsPiiConfig | None = None,
) -> RecordsMaskResult:
    """Mask a table. ``columns`` limits which columns are processed (default: all)."""
    data = [dict(r) for r in rows]
    cfg = config or get_config()
    harbor = cfg.pii.hipaa_safe_harbor if safe_harbor is None else safe_harbor
    cols = list(columns) if columns is not None else list(dict.fromkeys(
        k for r in data for k in r))  # fmt: skip
    session = MaskSession(reversible=reversible)
    try:
        types = classify_columns(data, cols, cfg, tenant_id=tenant_id)
        counts: Counter[str] = Counter()
        for row in data:
            for col in cols:
                raw = row.get(col)
                if raw is None or str(raw).strip() == "":
                    continue
                value = str(raw)
                kind = types[col]
                if kind == "ZIP_CODE":
                    row[col] = _generalise_zip(value.strip()) if harbor else value
                    counts["ZIP_CODE"] += int(harbor)
                elif kind is not None:
                    row[col] = session.placeholder(kind, value.strip())
                    counts[kind] += 1
                elif harbor and DATE_HEADER.search(_norm_header(col)):
                    row[col] = _generalise_date(value)
                    counts["DATE"] += 1
                elif isinstance(raw, str) and any(c.isalpha() for c in value):
                    res = session.mask(value, tenant_id=tenant_id, safe_harbor=harbor, config=cfg)
                    row[col] = res.masked_text
                    counts.update(res.counts)
        session._store()
    except GuardrailError:
        raise
    except Exception as exc:
        raise GuardrailError("PII masking of records failed; blocked (fail closed)") from exc
    return RecordsMaskResult(data, types, session.mapping_id, dict(counts), len(data))


@dataclass(frozen=True)
class PrivacyReport:
    """Counts only. Safe to log, store or show in a UI."""

    entity_counts: dict[str, int]
    flagged_columns: list[str]
    rows_scanned: int
    texts_scanned: int

    def as_dict(self) -> dict[str, Any]:
        return {"entity_counts": self.entity_counts, "flagged_columns": self.flagged_columns,
                "rows_scanned": self.rows_scanned, "texts_scanned": self.texts_scanned}  # fmt: skip


def privacy_report(*results: MaskResult | RecordsMaskResult) -> PrivacyReport:
    """Combine mask results into entity counts and flagged columns (never values)."""
    counts: Counter[str] = Counter()
    flagged: list[str] = []
    rows = texts = 0
    for r in results:
        if isinstance(r, RecordsMaskResult):
            counts.update(r.entity_counts)
            flagged += [c for c in r.flagged_columns if c not in flagged]
            rows += r.rows_scanned
        else:
            counts.update(r.counts)
            texts += 1
    return PrivacyReport(dict(counts), flagged, rows, texts)
