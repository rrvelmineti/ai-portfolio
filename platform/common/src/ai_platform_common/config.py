"""Typed access to environment-variable configuration.

Secrets are only ever read from the process environment (populated from a local,
gitignored ``.env`` in development or from the runtime's secret store in production).
Nothing in this module logs or exposes secret values.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def get_env(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Return the value of ``name`` or ``None`` when it is unset or blank."""
    env = os.environ if environ is None else environ
    value = env.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def get_bool(name: str, default: bool = False, environ: Mapping[str, str] | None = None) -> bool:
    """Interpret an env var as a boolean flag (``1/true/yes/on`` are true)."""
    value = get_env(name, environ)
    if value is None:
        return default
    return value.lower() in _TRUTHY


def has_all(names: tuple[str, ...], environ: Mapping[str, str] | None = None) -> bool:
    """Return True when every variable in ``names`` is set and non-blank."""
    return all(get_env(n, environ) is not None for n in names)


def missing(names: tuple[str, ...], environ: Mapping[str, str] | None = None) -> list[str]:
    """Return the subset of ``names`` that is unset or blank."""
    return [n for n in names if get_env(n, environ) is None]


def debug_log_prompts(environ: Mapping[str, str] | None = None) -> bool:
    """Whether prompt/response text may be written to logs (off by default)."""
    return get_bool("DEBUG_LOG_PROMPTS", default=False, environ=environ)
