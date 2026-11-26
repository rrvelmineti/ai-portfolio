"""Tests for the ai_platform_common package metadata."""

import re

import ai_platform_common


def test_get_version_returns_semver() -> None:
    version = ai_platform_common.get_version()

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_get_version_matches_dunder_version() -> None:
    assert ai_platform_common.get_version() == ai_platform_common.__version__
