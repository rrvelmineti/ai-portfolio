"""Public data types for the model gateway."""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TextPart(BaseModel):
    """A plain-text content part."""

    model_config = ConfigDict(frozen=True)

    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """An inline image, base64-encoded (no ``data:`` prefix)."""

    model_config = ConfigDict(frozen=True)

    type: Literal["image"] = "image"
    data: str
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]

    @classmethod
    def from_bytes(cls, raw: bytes, media_type: str) -> ImagePart:
        """Build an image part from raw bytes."""
        return cls.model_validate(
            {"data": base64.b64encode(raw).decode("ascii"), "media_type": media_type}
        )

    def raw_bytes(self) -> bytes:
        """Return the decoded image bytes."""
        return base64.b64decode(self.data)


ContentPart = Annotated[TextPart | ImagePart, Field(discriminator="type")]


class Message(BaseModel):
    """One conversation turn. System instructions go in ``complete(system=...)``."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str | list[ContentPart]

    def parts(self) -> list[TextPart | ImagePart]:
        """Return the content as a list of parts."""
        if isinstance(self.content, str):
            return [TextPart(text=self.content)]
        return list(self.content)

    def text(self) -> str:
        """Concatenate the text parts."""
        return "".join(p.text for p in self.parts() if isinstance(p, TextPart))

    def has_images(self) -> bool:
        """Whether any part is an image."""
        return any(isinstance(p, ImagePart) for p in self.parts())


class AttemptRecord(BaseModel):
    """What happened with one entry of an alias's fallback chain.

    ``reason`` is written by the gateway (never copied from provider error text) so it
    is safe to log.
    """

    provider: str
    model: str
    outcome: Literal["ok", "failed", "skipped"]
    error_type: str | None = None
    reason: str | None = None
    attempts: int = 0


class CompletionResult(BaseModel):
    """Result of :func:`ai_platform_gateway.complete`."""

    text: str
    parsed: Any = None
    provider: str
    model: str
    alias: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: float
    request_id: str
    fallback_used: bool
    attempts: int
    finish_reason: str | None = None
    attempt_records: list[AttemptRecord] = Field(default_factory=list)
    # Guardrails (counts and codes only; never values)
    pii_mode: str = "mask"
    mapping_id: str | None = None  # for unmask() within the same request
    pii_entities: dict[str, int] = Field(default_factory=dict)
    injection_score: float | None = None
    guard_flags: list[str] = Field(default_factory=list)  # input side, e.g. untrusted content
    output_flags: list[str] = Field(default_factory=list)  # e.g. output_pii:PERSON


class EmbeddingResult(BaseModel):
    """Result of :func:`ai_platform_gateway.embed`."""

    vectors: list[list[float]]
    provider: str
    model: str
    alias: str
    dimensions: int
    tokens: int | None
    cost_usd: float | None
    latency_ms: float
    request_id: str
    fallback_used: bool = False
    attempts: int = 1
    attempt_records: list[AttemptRecord] = Field(default_factory=list)
    pii_mode: str = "mask"
    mapping_id: str | None = None
    pii_entities: dict[str, int] = Field(default_factory=dict)
