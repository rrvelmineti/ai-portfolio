"""The Presidio analyzer (loaded once per process) and span selection."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from functools import lru_cache

from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpEngineProvider

from ai_platform_guardrails.pii.config import (
    CANONICAL,
    PRESIDIO_NAMES,
    PRIORITY,
    SAFE_HARBOR_ENTITIES,
    GuardrailsPiiConfig,
    TenantConfig,
    spacy_model_name,
)
from ai_platform_guardrails.pii.recognizers import default_recognizers

_lock = threading.Lock()


@dataclass(frozen=True)
class Detection:
    """One detected entity. ``entity`` is our canonical name; values are never kept here."""

    entity: str
    start: int
    end: int
    score: float


@lru_cache(maxsize=4)
def get_analyzer(model_name: str | None = None) -> AnalyzerEngine:
    """Build the analyzer with our recognizers. Cached: the spaCy model loads once."""
    name = model_name or spacy_model_name()
    # Presidio's DEBUG logs include context words from the analysed text: never emit them.
    logging.getLogger("presidio-analyzer").setLevel(logging.WARNING)
    with _lock:
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": name}],
            }
        )
        engine = AnalyzerEngine(
            nlp_engine=provider.create_engine(),
            supported_languages=["en"],
            context_aware_enhancer=LemmaContextAwareEnhancer(
                context_prefix_count=3, context_suffix_count=1
            ),
        )
        for recognizer in default_recognizers():
            engine.registry.add_recognizer(recognizer)
    return engine


def tenant_recognizers(tenant: TenantConfig | None) -> list[EntityRecognizer]:
    if tenant is None:
        return []
    return [
        PatternRecognizer(
            supported_entity=PRESIDIO_NAMES.get(spec.entity, spec.entity),
            name=f"tenant_{spec.entity}_{i}",
            patterns=[Pattern(p.name, p.regex, p.score) for p in spec.patterns],
            context=spec.context,
        )
        for i, spec in enumerate(tenant.recognizers)
    ]


def detect(
    text: str,
    config: GuardrailsPiiConfig,
    *,
    tenant_id: str | None = None,
    safe_harbor: bool = False,
    extra_context: list[str] | None = None,
) -> list[Detection]:
    """Detect entities and resolve overlaps (non-overlapping, sorted by position)."""
    if not text.strip():
        return []
    analyzer = get_analyzer()
    entities = [PRESIDIO_NAMES[e] for e in config.entities]
    if safe_harbor:
        entities += list(SAFE_HARBOR_ENTITIES)
    tenant = config.tenants.get(tenant_id) if tenant_id else None
    results = analyzer.analyze(
        text,
        language="en",
        entities=entities,
        score_threshold=config.pii.score_threshold,
        ad_hoc_recognizers=tenant_recognizers(tenant) or None,
        context=extra_context,
    )
    return _select(text, results)


_POSSESSIVES = ("'s", "\u2019s")


def _select(text: str, results: list[RecognizerResult]) -> list[Detection]:
    """Keep the best non-overlapping spans: higher priority, then score, then length."""
    ranked = sorted(
        results,
        key=lambda r: (
            PRIORITY.get(CANONICAL.get(r.entity_type, r.entity_type), 10),
            r.score,
            r.end - r.start,
        ),
        reverse=True,
    )
    chosen: list[Detection] = []
    for r in ranked:
        if any(r.start < c.end and c.start < r.end for c in chosen):
            continue
        entity = CANONICAL.get(r.entity_type, r.entity_type)
        end = r.end
        if entity == "PERSON" and text[r.start : end].endswith(_POSSESSIVES):
            end -= 2  # "Ana Ruiz's" -> "Ana Ruiz", so the placeholder is reused
        chosen.append(Detection(entity, r.start, end, r.score))
    return sorted(chosen, key=lambda d: d.start)
