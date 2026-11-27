"""Guardrails PII configuration: entity set, Safe Harbor switch, per-tenant recognizers.

YAML (path from ``GUARDRAILS_CONFIG``; optional)::

    pii:
      mask_location: false          # cities/states (spaCy GPE/LOC); off by default
      hipaa_safe_harbor: false      # dates -> year, ZIP -> first 3 digits, age > 89 -> 90+
      score_threshold: 0.5
    tenants:
      "6d1f73f0-....":              # tenant_id from the access token
        recognizers:
          - entity: MEMBER_ID
            patterns:
              - {name: acme_member, regex: "\\\\bACM-\\\\d{7}\\\\b", score: 0.6}
            context: [member, acme id]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from ai_platform_common.config import get_env
from pydantic import BaseModel, ConfigDict, Field

# Our canonical entity names -> Presidio entity names.
PRESIDIO_NAMES: dict[str, str] = {
    "PERSON": "PERSON",
    "EMAIL": "EMAIL_ADDRESS",
    "PHONE": "PHONE_NUMBER",
    "US_SSN": "US_SSN",
    "DATE_OF_BIRTH": "DATE_OF_BIRTH",
    "ADDRESS": "ADDRESS",
    "LOCATION": "LOCATION",
    "CREDIT_CARD": "CREDIT_CARD",
    "US_BANK_NUMBER": "US_BANK_NUMBER",
    "IP_ADDRESS": "IP_ADDRESS",
    "MEMBER_ID": "MEMBER_ID",
    "CLAIM_NUMBER": "CLAIM_NUMBER",
    "POLICY_NUMBER": "POLICY_NUMBER",
    "NPI": "NPI",
}
CANONICAL: dict[str, str] = {v: k for k, v in PRESIDIO_NAMES.items()}
# Detected only for Safe Harbor generalisation, never masked as placeholders.
SAFE_HARBOR_ENTITIES = ("DATE_TIME", "ZIP_CODE", "AGE")

MASKED_ENTITIES: tuple[str, ...] = tuple(k for k in PRESIDIO_NAMES if k != "LOCATION")

# Priority when spans overlap (higher wins): specific formats beat generic NER.
PRIORITY: dict[str, int] = {
    "US_SSN": 100, "CREDIT_CARD": 100, "NPI": 95, "EMAIL": 95, "IP_ADDRESS": 90,
    "MEMBER_ID": 90, "CLAIM_NUMBER": 90, "POLICY_NUMBER": 90, "DATE_OF_BIRTH": 85,
    "ADDRESS": 80, "PHONE": 75, "US_BANK_NUMBER": 60, "PERSON": 50, "LOCATION": 40,
    "AGE": 35, "ZIP_CODE": 33, "DATE_TIME": 30,
}  # fmt: skip


class PatternSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    regex: str
    score: float = Field(ge=0, le=1)


class RecognizerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str
    patterns: list[PatternSpec] = Field(min_length=1)
    context: list[str] = Field(default_factory=list)


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recognizers: list[RecognizerSpec] = Field(default_factory=list)


class PiiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mask_location: bool = False
    hipaa_safe_harbor: bool = False
    score_threshold: float = 0.5


class GuardrailsPiiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pii: PiiSettings = Field(default_factory=PiiSettings)
    tenants: dict[str, TenantConfig] = Field(default_factory=dict)

    @property
    def entities(self) -> list[str]:
        """Canonical entity names to mask."""
        return [*MASKED_ENTITIES, *(["LOCATION"] if self.pii.mask_location else [])]


def load_pii_config(path: Path | str | None = None) -> GuardrailsPiiConfig:
    """Load the YAML file (``GUARDRAILS_CONFIG``), or defaults when none is set."""
    target = path or get_env("GUARDRAILS_CONFIG")
    if target is None:
        return GuardrailsPiiConfig()
    raw: dict[str, Any] = yaml.safe_load(Path(target).read_text(encoding="utf-8")) or {}
    return GuardrailsPiiConfig.model_validate({k: raw[k] for k in ("pii", "tenants") if k in raw})


def spacy_model_name() -> str:
    """spaCy model for Presidio (``GUARDRAILS_SPACY_MODEL``, default ``en_core_web_lg``)."""
    return get_env("GUARDRAILS_SPACY_MODEL") or "en_core_web_lg"
