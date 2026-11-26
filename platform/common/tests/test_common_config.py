import pytest
from ai_platform_common import config


def test_get_env_blank_is_none() -> None:
    assert config.get_env("X", {"X": "  "}) is None
    assert config.get_env("X", {}) is None
    assert config.get_env("X", {"X": " v "}) == "v"


def test_get_env_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PORTFOLIO_TEST_VAR", "abc")
    assert config.get_env("AI_PORTFOLIO_TEST_VAR") == "abc"


def test_get_bool() -> None:
    assert config.get_bool("F", environ={"F": "TRUE"}) is True
    assert config.get_bool("F", environ={"F": "0"}) is False
    assert config.get_bool("F", default=True, environ={}) is True


def test_has_all_and_missing() -> None:
    env = {"A": "1", "B": ""}
    assert config.has_all(("A",), env) is True
    assert config.has_all(("A", "B"), env) is False
    assert config.missing(("A", "B", "C"), env) == ["B", "C"]


def test_debug_log_prompts_default_off() -> None:
    assert config.debug_log_prompts({}) is False
    assert config.debug_log_prompts({"DEBUG_LOG_PROMPTS": "true"}) is True
