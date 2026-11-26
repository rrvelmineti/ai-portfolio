"""Structured output: ask for JSON, validate with pydantic, one repair retry."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def json_instruction(schema: type[BaseModel]) -> str:
    """System-prompt text asking for a JSON object that matches ``schema``."""
    return (
        "Respond with only a single JSON object (no prose, no code fences) that "
        "validates against this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )


def merge_system(system: str | None, schema: type[BaseModel] | None) -> str | None:
    """Append the JSON instruction to the caller's system prompt when a schema is set."""
    if schema is None:
        return system
    instruction = json_instruction(schema)
    return f"{system}\n\n{instruction}" if system else instruction


def strip_fences(text: str) -> str:
    """Remove a surrounding Markdown code fence, if present."""
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


def parse(text: str, schema: type[BaseModel]) -> BaseModel:
    """Validate model output; raises ``pydantic.ValidationError`` on failure."""
    return schema.model_validate_json(strip_fences(text))


def repair_prompt(error: ValidationError) -> str:
    """Follow-up user message describing the validation failure."""
    return (
        "Your previous reply did not validate against the JSON Schema. Errors:\n"
        f"{error}\n"
        "Reply again with only the corrected JSON object."
    )
