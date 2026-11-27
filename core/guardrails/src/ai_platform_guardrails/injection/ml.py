"""Local ML prompt-injection classifier behind a swappable interface (ADR 0007).

The default implementation is Horizon-Labs ``prompt-injection-guard-small`` (int8 ONNX,
Apache-2.0, weights and tokenizer derived from MIT-licensed mmBERT-small), pinned to one
Hub revision. Supply-chain rules:

- **Only data files are loaded:** the ONNX graph, ``tokenizer.json`` and ``config.json``
  (for the label names). No pickle files, no ``transformers``, no remote code.
- **Every file is checked** against its pinned size and SHA-256 (the Hub's own LFS hashes;
  the publisher's ``SHA256SUMS`` file was found stale) before it is used. A mismatch fails
  closed.
- **Licences travel with the model:** ``LICENSE`` (Apache-2.0) and ``NOTICE`` (mmBERT MIT)
  are downloaded and verified with it.

Files are fetched by ``scripts/download_guard_model.py``; this module never downloads.
Long inputs are scored in overlapping 512-token windows and the highest window counts.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from ai_platform_common.config import get_bool, get_env
from ai_platform_gateway.errors import GuardrailError

WINDOW_TOKENS = 512
WINDOW_OVERLAP = 128
MAX_WINDOWS = 64  # about 24k tokens; longer content is flagged as only partly scanned


@dataclass(frozen=True)
class ModelFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    revision: str
    model_file: str
    tokenizer_file: str
    config_file: str
    injection_label: str
    files: tuple[ModelFile, ...]

    @property
    def url_base(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/"


GUARD_SMALL = ModelSpec(
    name="prompt-injection-guard-small",
    repo="Horizon-Labs/prompt-injection-guard-small",
    revision="0c8139d109994806ff404b5b693ad5d63629b9ba",  # v2.7
    model_file="onnx/model_quantized.onnx",
    tokenizer_file="tokenizer.json",
    config_file="config.json",
    injection_label="INJECTION",
    files=(
        ModelFile("onnx/model_quantized.onnx",
                  "5e4511b71f5010358ea714c3dd829187f3c11f3dbbf7f59bf5746b74ed804ef7", 268409234),
        ModelFile("tokenizer.json",
                  "f90a8f9bb9f8e1dc73296a6c838548018f8a9a1d7ee19a2b0240aa47cf629fa5", 34387758),
        ModelFile("config.json",
                  "d6264037c9aa4b3d821c1c9a0329572033adbdb622788ecca91c3636066d9622", 2031),
        ModelFile("LICENSE",
                  "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30", 11358),
        ModelFile("NOTICE",
                  "5ffe2383027c7510933df2e7055440edde8c96b67b7a58fb477f9632c72b9c14", 1566),
    ),
)  # fmt: skip


class ModelUnavailable(GuardrailError):
    """The classifier files are missing or fail their integrity check (fail closed)."""


@dataclass(frozen=True)
class ClassifierScore:
    score: float  # P(injection) of the highest-scoring window
    windows: int
    truncated: bool  # True when the text needed more than MAX_WINDOWS windows


class PromptClassifier(Protocol):
    """Anything that scores text for prompt injection (0 = benign, 1 = attack)."""

    name: str

    def score(self, text: str) -> ClassifierScore: ...


def models_root() -> Path:
    configured = get_env("GUARD_MODEL_DIR")
    return Path(configured) if configured else Path.home() / ".cache" / "ai-platform" / "models"


def model_dir(spec: ModelSpec = GUARD_SMALL, root: Path | None = None) -> Path:
    return (root or models_root()) / spec.name / spec.revision[:12]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_files(spec: ModelSpec, directory: Path) -> None:
    """Raise ``ModelUnavailable`` unless every pinned file is present and intact."""
    problems = []
    for f in spec.files:
        p = directory / f.path
        if not p.is_file():
            problems.append(f"{f.path}: missing")
        elif p.stat().st_size != f.size:
            problems.append(f"{f.path}: size {p.stat().st_size} != {f.size}")
        elif sha256_file(p) != f.sha256:
            problems.append(f"{f.path}: SHA-256 mismatch")
    if problems:
        raise ModelUnavailable(
            f"{spec.name}@{spec.revision[:7]} unusable in {directory} ({'; '.join(problems)}). "
            "Run: uv run python scripts/download_guard_model.py"
        )


def windows(ids: list[int], size: int, overlap: int, limit: int) -> tuple[list[list[int]], bool]:
    """Split token ids into overlapping windows; returns (windows, truncated)."""
    if size <= overlap:
        raise ValueError("window size must exceed the overlap")
    if len(ids) <= size:
        return [ids], False
    step = size - overlap
    needed = 1 + math.ceil((len(ids) - size) / step)
    out = [ids[i * step : i * step + size] for i in range(min(needed, limit))]
    return out, needed > limit


class OnnxPromptClassifier:
    """Sequence classifier run with ONNX Runtime on CPU, from verified local files."""

    def __init__(
        self,
        spec: ModelSpec = GUARD_SMALL,
        directory: Path | None = None,
        *,
        threads: int | None = None,
        verify: bool = True,
    ) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.name = f"{spec.name}@{spec.revision[:7]}"
        root = directory or model_dir(spec)
        if verify:
            verify_files(spec, root)
        config = json.loads((root / spec.config_file).read_text(encoding="utf-8"))
        labels = {int(k): str(v).upper() for k, v in config["id2label"].items()}
        matches = [k for k, v in labels.items() if v == spec.injection_label]
        if len(matches) != 1:
            raise ModelUnavailable(f"{self.name}: label {spec.injection_label!r} not in {labels}")
        self._injection_index = matches[0]
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads if threads is not None else _threads()
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(root / spec.model_file), opts, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(str(root / spec.tokenizer_file))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        specials = self._tokenizer.encode("").ids  # e.g. [<bos>, <eos>] or [CLS, SEP]
        self._prefix, self._suffix = specials[:1], specials[1:]
        self._np = np

    def _window_prob(self, ids: list[int]) -> float:
        np = self._np
        full = [*self._prefix, *ids, *self._suffix]
        feed: dict[str, Any] = {
            "input_ids": np.array([full], dtype=np.int64),
            "attention_mask": np.ones((1, len(full)), dtype=np.int64),
        }
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros((1, len(full)), dtype=np.int64)
        logits = self._session.run(None, feed)[0][0]
        exp = np.exp(logits - logits.max())
        return float(exp[self._injection_index] / exp.sum())

    def score(self, text: str) -> ClassifierScore:
        if not text.strip():
            return ClassifierScore(0.0, 0, False)
        ids = self._tokenizer.encode(text, add_special_tokens=False).ids
        size = WINDOW_TOKENS - len(self._prefix) - len(self._suffix)
        parts, truncated = windows(ids, size, WINDOW_OVERLAP, MAX_WINDOWS)
        best = max(self._window_prob(p) for p in parts)
        return ClassifierScore(round(best, 4), len(parts), truncated)


def _threads() -> int:
    value = os.environ.get("GUARD_ML_THREADS", "")
    return int(value) if value.isdigit() else 0  # 0 = ONNX Runtime default


def ml_classifier_enabled() -> bool:
    return get_bool("GUARD_ML_CLASSIFIER", True)


_override: PromptClassifier | None = None
_factory: Callable[[], PromptClassifier] = OnnxPromptClassifier


def set_prompt_classifier(classifier: PromptClassifier | None) -> None:
    """Replace the classifier (tests, or another implementation). ``None`` restores it."""
    global _override
    _override = classifier


@lru_cache(maxsize=1)
def _default() -> PromptClassifier:
    return _factory()


def get_prompt_classifier() -> PromptClassifier | None:
    """The active classifier, or ``None`` when ``GUARD_ML_CLASSIFIER=false``.

    Raises ``ModelUnavailable`` when it is enabled but its files are missing or tampered
    with, so callers fail closed.
    """
    if _override is not None:
        return _override
    if not ml_classifier_enabled():
        return None
    return _default()
