from pathlib import Path

import pytest
from ai_platform_gateway.config import DEFAULT_MODELS_PATH, ModelEntry, default_config, load_config
from pydantic import ValidationError

REQUIRED_ALIASES = {
    "default", "best", "fast", "vision", "default-embed",
    "claude-sonnet", "openai-mini", "gemini-flash", "groq-llama",
}  # fmt: skip


def test_bundled_config_loads_with_required_aliases() -> None:
    cfg = default_config()
    assert set(cfg.aliases) >= REQUIRED_ALIASES
    assert cfg.models["anthropic/claude-sonnet-5-5"].key == "anthropic/claude-sonnet-5-5"


def test_alias_chains_match_agreed_routing() -> None:
    cfg = default_config()
    assert [e.key for e in cfg.chain("default")] == [
        "anthropic/claude-sonnet-5-5",
        "bedrock/claude-sonnet-5-5",
        "openai/gpt-4o",
    ]
    assert [e.provider for e in cfg.chain("best")] == ["anthropic", "bedrock"]
    assert cfg.chain("best")[0].model == "claude-opus-5-5"


def test_openai_mini_alias_uses_recommended_small_model() -> None:
    cfg = default_config()
    assert "gpt-4o-mini" not in cfg.aliases
    assert [e.model for e in cfg.chain("openai-mini")][0] == "gpt-6-luna"
    assert cfg.chain("fast")[-1].model == "gpt-6-luna"
    assert all(m.model != "gpt-4o-mini" for m in cfg.models.values())


def test_claude_entries_disable_temperature() -> None:
    cfg = default_config()
    for entry in cfg.models.values():
        if "claude" in entry.model:
            assert entry.supports_temperature is False, entry.key


def test_unverified_prices() -> None:
    """Lists every catalog entry without a confirmed price so it stays visible.

    Run ``uv run pytest -k unverified -s`` to print the list.
    """
    cfg = default_config()
    unverified = sorted(k for k, m in cfg.models.items() if not m.price_verified)
    print("\nUnverified prices (cost_usd will be null):")  # noqa: T201
    for key in unverified:
        print(f"  - {key}")  # noqa: T201
    # Every null price must carry a "# verify:" comment in models.yaml.
    text = DEFAULT_MODELS_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "price" in line and ": null" in line:
            assert "# verify:" in line, line
    # Every verified entry really has both prices (or is an embedding model).
    for key, m in cfg.models.items():
        if key not in unverified:
            assert m.price_input_per_1m is not None


def test_env_reference_model_ids() -> None:
    entry = ModelEntry(provider="azure_openai", model="${AZURE_OPENAI_DEPLOYMENT}")
    assert entry.model_env_var == "AZURE_OPENAI_DEPLOYMENT"
    assert entry.missing_env({}) == ["AZURE_OPENAI_DEPLOYMENT"]
    assert entry.missing_env({"AZURE_OPENAI_DEPLOYMENT": "dep"}) == []
    assert entry.resolved_model({"AZURE_OPENAI_DEPLOYMENT": "dep"}) == "dep"
    plain = ModelEntry(provider="openai", model="gpt-4o")
    assert plain.model_env_var is None
    assert plain.resolved_model() == "gpt-4o"
    assert plain.missing_env({}) == []


def test_price_verified_rules() -> None:
    assert not ModelEntry(provider="x", model="m").price_verified
    assert not ModelEntry(provider="x", model="m", price_input_per_1m=1).price_verified
    assert ModelEntry(provider="x", model="m", kind="embed", price_input_per_1m=1).price_verified


def test_unknown_reference_rejected(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("models: {}\naliases:\n  a: {kind: chat, fallback: [nope]}\n")
    with pytest.raises(ValidationError, match="unknown model"):
        load_config(p)


def test_kind_mismatch_rejected(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text(
        "models:\n  e: {provider: openai, model: x, kind: embed}\n"
        "aliases:\n  a: {kind: chat, fallback: [e]}\n"
    )
    with pytest.raises(ValidationError, match="is chat but"):
        load_config(p)


def test_demo_free_alias_uses_only_free_tier_providers() -> None:
    """The public demo must never reach a paid provider (ADR 0008)."""
    chain = default_config().chain("demo-free")
    assert [e.key for e in chain] == ["gemini/gemini-3.8-flash", "groq/llama-3.3-70b-versatile"]
    assert {e.provider for e in chain} <= {"gemini", "groq"}
