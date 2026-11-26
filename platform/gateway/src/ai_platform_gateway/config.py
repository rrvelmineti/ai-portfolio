"""Load and validate ``models.yaml`` (model catalog + aliases + fallback chains)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from ai_platform_common.config import get_env
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MODELS_PATH = Path(__file__).with_name("models.yaml")

ProviderName = Literal["anthropic", "openai", "azure_openai", "bedrock", "gemini", "groq"]

_ENV_REF = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


class ModelEntry(BaseModel):
    """One concrete model on one provider, with prices (USD per 1M tokens)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = ""
    provider: str
    model: str = Field(description="Provider model id, or ${ENV_VAR} to read it at call time.")
    kind: Literal["chat", "embed"] = "chat"
    price_input_per_1m: float | None = None
    price_output_per_1m: float | None = None
    # Optional second price tier for long prompts (e.g. Claude Haiku 5.5 > 100K tokens).
    long_prompt_threshold_tokens: int | None = None
    long_prompt_price_input_per_1m: float | None = None
    long_prompt_price_output_per_1m: float | None = None
    context_limit: int | None = None
    supports_temperature: bool = True
    supports_images: bool = False
    # Embedding sizes the model can return; None means "any size the alias asks for".
    supported_dimensions: list[int] | None = None

    @property
    def model_env_var(self) -> str | None:
        """The env var named by a ``${VAR}`` model id, if any."""
        match = _ENV_REF.match(self.model)
        return match.group(1) if match else None

    def resolved_model(self, environ: Mapping[str, str] | None = None) -> str:
        """Return the concrete model id, resolving ``${VAR}`` references."""
        var = self.model_env_var
        if var is None:
            return self.model
        return get_env(var, environ) or ""

    def missing_env(self, environ: Mapping[str, str] | None = None) -> list[str]:
        """Env vars this entry itself needs (beyond the provider's own)."""
        var = self.model_env_var
        return [var] if var and get_env(var, environ) is None else []

    @property
    def price_verified(self) -> bool:
        """True when every price this entry needs is known."""
        if self.price_input_per_1m is None:
            return False
        return self.kind == "embed" or self.price_output_per_1m is not None


class AliasConfig(BaseModel):
    """A named route: an ordered fallback chain of catalog entries."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["chat", "embed"]
    description: str = ""
    dimensions: int | None = None
    fallback: list[str] = Field(min_length=1)


class GatewayConfig(BaseModel):
    """The whole ``models.yaml`` document."""

    model_config = ConfigDict(extra="forbid")

    models: dict[str, ModelEntry]
    aliases: dict[str, AliasConfig]

    @model_validator(mode="after")
    def _check_references(self) -> GatewayConfig:
        for key, entry in self.models.items():
            if not entry.key:
                self.models[key] = entry.model_copy(update={"key": key})
        for name, alias in self.aliases.items():
            for ref in alias.fallback:
                if ref not in self.models:
                    raise ValueError(f"alias {name!r} references unknown model {ref!r}")
                if self.models[ref].kind != alias.kind:
                    raise ValueError(
                        f"alias {name!r} is {alias.kind} but {ref!r} is {self.models[ref].kind}"
                    )
        return self

    def chain(self, alias: str) -> list[ModelEntry]:
        """The ordered fallback entries for ``alias``."""
        return [self.models[k] for k in self.aliases[alias].fallback]


def load_config(path: Path | str | None = None) -> GatewayConfig:
    """Parse and validate a models file (defaults to the bundled ``models.yaml``)."""
    raw = yaml.safe_load(Path(path or DEFAULT_MODELS_PATH).read_text(encoding="utf-8"))
    return GatewayConfig.model_validate(raw)


@lru_cache(maxsize=1)
def default_config() -> GatewayConfig:
    """The bundled configuration, loaded once."""
    return load_config()
