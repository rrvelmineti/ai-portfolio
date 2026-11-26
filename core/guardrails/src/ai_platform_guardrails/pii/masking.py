"""``mask_text`` / ``unmask``: typed, consistent placeholders and a request-scoped vault.

* Placeholders are typed and numbered per masking session: the same value becomes the
  same ``<PERSON_1>`` everywhere in that session (one ``mask_text`` call, or one
  ``MaskSession`` shared across several texts, e.g. all messages of one AI call).
* The placeholder -> value mapping is stored only in the request context's ``vault``
  (memory, never logged, traced or persisted) and disappears when the request ends.
* HIPAA Safe Harbor mode generalises instead of masking: dates -> year, ZIP -> first
  three digits (000 for restricted prefixes), ages over 89 -> "90+". Not reversible.
* Fail closed: any error inside detection or masking raises ``GuardrailError``.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from ai_platform_common.context import current
from ai_platform_common.logging import get_logger
from ai_platform_gateway.errors import GuardrailError

from ai_platform_guardrails.pii.config import GuardrailsPiiConfig, load_pii_config
from ai_platform_guardrails.pii.engine import Detection, detect

log = get_logger("ai_platform_guardrails.pii")

VAULT_KEY = "pii_mappings"
PLACEHOLDER_RE = re.compile(r"<([A-Z_]+)_(\d+)>")

# 3-digit ZIP prefixes with < 20,000 people (HHS Safe Harbor guidance, 2000 Census);
# these must be replaced with 000.
RESTRICTED_ZIP3 = frozenset({
    "036", "059", "063", "102", "203", "556", "692", "790", "821", "823", "830", "831",
    "878", "879", "884", "890", "893",
})  # fmt: skip
_YEAR = re.compile(r"\b(18|19|20)\d{2}\b")
_NUMBER = re.compile(r"\d{1,3}")


class MappingExpired(LookupError):
    """The mapping is gone (request ended) or never existed."""


@dataclass(frozen=True)
class EntityInfo:
    """A detected entity: type, position in the *original* text, score. Never the value."""

    type: str
    start: int
    end: int
    score: float


@dataclass
class MaskResult:
    masked_text: str
    entities: list[EntityInfo]
    mapping_id: str | None
    generalized: dict[str, int] = field(default_factory=dict)  # Safe Harbor counts

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(e.type for e in self.entities))

    def __repr__(self) -> str:  # never show text in reprs/tracebacks
        return f"MaskResult(entities={self.counts}, mapping_id={self.mapping_id!r})"


_config_cache: dict[str, GuardrailsPiiConfig] = {}


def get_config() -> GuardrailsPiiConfig:
    if "c" not in _config_cache:
        _config_cache["c"] = load_pii_config()
    return _config_cache["c"]


def set_config(config: GuardrailsPiiConfig | None) -> None:
    """Override the PII config (tests, products); ``None`` reloads from the environment."""
    _config_cache.pop("c", None)
    if config is not None:
        _config_cache["c"] = config


class MaskSession:
    """One placeholder namespace. Use one session per AI call so all messages agree."""

    def __init__(self, *, reversible: bool = True) -> None:
        self.mapping_id = f"pii-{uuid.uuid4().hex}" if reversible else None
        self._by_value: dict[tuple[str, str], str] = {}
        self._values: dict[str, str] = {}
        self._counters: Counter[str] = Counter()
        self.entities: list[EntityInfo] = []
        self.generalized: Counter[str] = Counter()

    def placeholder(self, entity: str, value: str) -> str:
        key = (entity, _normalise(value))
        if key not in self._by_value:
            self._counters[entity] += 1
            ph = f"<{entity}_{self._counters[entity]}>"
            self._by_value[key] = ph
            self._values[ph] = value
        return self._by_value[key]

    def mask(
        self,
        text: str,
        *,
        tenant_id: str | None = None,
        safe_harbor: bool | None = None,
        config: GuardrailsPiiConfig | None = None,
        extra_context: list[str] | None = None,
    ) -> MaskResult:
        """Mask one text within this session (fail closed)."""
        try:
            cfg = config or get_config()
            harbor = cfg.pii.hipaa_safe_harbor if safe_harbor is None else safe_harbor
            ctx = current()
            tid = tenant_id or (str(ctx.tenant_id) if ctx and ctx.tenant_id else None)
            found = detect(text, cfg, tenant_id=tid, safe_harbor=harbor,
                           extra_context=extra_context)  # fmt: skip
            masked = self._apply(text, found)
            self._store()
        except GuardrailError:
            raise
        except Exception as exc:
            log.error("guardrail.pii_failed", extra={"fields": {"error_type": type(exc).__name__}})
            raise GuardrailError("PII masking failed; call blocked (fail closed)") from exc
        new = [EntityInfo(d.entity, d.start, d.end, round(d.score, 3)) for d in found
               if d.entity not in ("DATE_TIME", "ZIP_CODE", "AGE")]  # fmt: skip
        self.entities.extend(new)
        return MaskResult(masked, new, self.mapping_id, dict(self.generalized))

    def _apply(self, text: str, found: Sequence[Detection]) -> str:
        out: list[str] = []
        pos = 0
        for d in found:
            value = text[d.start : d.end]
            out.append(text[pos : d.start])
            if d.entity == "DATE_TIME":
                out.append(_generalise_date(value))
                self.generalized["DATE"] += 1
            elif d.entity == "ZIP_CODE":
                out.append(_generalise_zip(value))
                self.generalized["ZIP_CODE"] += 1
            elif d.entity == "AGE":
                out.append(_generalise_age(value))
                self.generalized["AGE"] += 1
            else:
                out.append(self.placeholder(d.entity, value))
            pos = d.end
        out.append(text[pos:])
        return "".join(out)

    def _store(self) -> None:
        """Keep the reversible mapping in the request vault only (never elsewhere)."""
        if self.mapping_id is None or not self._values:
            return
        ctx = current()
        if ctx is None:
            return  # outside a request there is nowhere safe to keep it
        ctx.vault.setdefault(VAULT_KEY, {})[self.mapping_id] = dict(self._values)


def mask_text(
    text: str,
    *,
    tenant_id: str | None = None,
    safe_harbor: bool | None = None,
    reversible: bool = True,
    config: GuardrailsPiiConfig | None = None,
) -> MaskResult:
    """Mask PII in ``text`` with typed placeholders (one session per call)."""
    return MaskSession(reversible=reversible).mask(
        text, tenant_id=tenant_id, safe_harbor=safe_harbor, config=config
    )


def unmask(text: str, mapping_id: str) -> str:
    """Restore original values for placeholders from ``mapping_id`` (same request only)."""
    ctx = current()
    mappings = ctx.vault.get(VAULT_KEY, {}) if ctx else {}
    mapping = mappings.get(mapping_id)
    if mapping is None:
        raise MappingExpired("PII mapping not available (request ended or unknown id)")
    return PLACEHOLDER_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def drop_mapping(mapping_id: str) -> None:
    """Forget a mapping before the request ends."""
    ctx = current()
    if ctx is not None:
        ctx.vault.get(VAULT_KEY, {}).pop(mapping_id, None)


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


def _generalise_date(value: str) -> str:
    m = _YEAR.search(value)
    return m.group(0) if m else ("[DATE]" if any(c.isdigit() for c in value) else value)


def _generalise_zip(value: str) -> str:
    zip3 = value[:3]
    return f"{'000' if zip3 in RESTRICTED_ZIP3 else zip3}**"


def _generalise_age(value: str) -> str:
    m = _NUMBER.search(value)
    if m and int(m.group(0)) > 89:
        return value[: m.start()] + "90+" + value[m.end() :]
    return value
