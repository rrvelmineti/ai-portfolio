"""The target data dictionary (``data/dictionary/claims_v1.json``)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DICTIONARY_PATH = DATA_DIR / "dictionary" / "claims_v1.json"
CANONICAL_DATE = "YYYY-MM-DD"


@dataclass(frozen=True)
class RuleSpec:
    text: str  # e.g. "exactly 10 digits", "date YYYY-MM-DD"
    severity: str  # "blocker" | "warning"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    description: str
    type: str
    required: bool
    rules: tuple[RuleSpec, ...]


@dataclass(frozen=True)
class Dictionary:
    name: str
    version: int
    fields: tuple[FieldSpec, ...]

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def for_date_format(self, date_format: str, only: set[str] | None = None) -> Dictionary:
        """The same rules with the client's date format (and optionally fewer fields)."""
        fields = tuple(
            FieldSpec(f.name, f.description, f.type, f.required, tuple(
                RuleSpec(f"date {date_format}" if r.text == f"date {CANONICAL_DATE}" else r.text,
                         r.severity) for r in f.rules))
            for f in self.fields if only is None or f.name in only
        )  # fmt: skip
        return Dictionary(self.name, self.version, fields)


@cache
def load_dictionary(path: Path = DICTIONARY_PATH) -> Dictionary:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = tuple(
        FieldSpec(f["name"], f["description"], f["type"], bool(f["required"]),
                  tuple(RuleSpec(r["rule"], r["severity"]) for r in f["rules"]))
        for f in raw["fields"]
    )  # fmt: skip
    return Dictionary(raw["name"], int(raw["version"]), fields)
