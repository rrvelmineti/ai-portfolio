"""Repo-wide pytest setup: keep the stdlib ``platform`` module from being shadowed.

With ``--import-mode=importlib`` pytest names test modules by their path from the
repo root (e.g. ``platform.common.tests.test_x``). When a parent name such as
``platform`` is already in ``sys.modules`` but has no ``__path__`` (true for the
stdlib module), pytest re-imports it from the path, replacing the stdlib
``platform`` with our top-level ``platform/`` folder as a namespace package.
botocore, truststore and others then fail on ``platform.system()``.

Giving the already-imported stdlib module an empty ``__path__`` stops that: pytest
attaches its placeholder sub-modules (``platform.common`` ...) to the real stdlib
module instead of replacing it. Test-only; guarded by
``platform/common/tests/test_stdlib_platform.py``.
"""

import platform

if not hasattr(platform, "__path__"):
    platform.__path__ = []  # type: ignore[attr-defined]
