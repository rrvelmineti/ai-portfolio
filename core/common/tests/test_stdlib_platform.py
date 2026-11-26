import sys


def test_stdlib_platform_not_shadowed() -> None:
    """No repo folder may replace the stdlib ``platform`` module during test runs.

    The shared code used to live in a top-level ``platform/`` folder, which pytest's
    importlib mode swapped in for the stdlib module (see ADR 0001, revision 2026-11-26).
    """
    mod = sys.modules["platform"]
    assert callable(getattr(mod, "system", None))
    assert getattr(mod, "__file__", None) is not None
