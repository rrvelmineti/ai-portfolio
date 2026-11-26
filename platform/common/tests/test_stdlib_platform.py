import sys


def test_stdlib_platform_not_shadowed() -> None:
    """The repo's ``platform/`` folder must never replace the stdlib ``platform`` module."""
    mod = sys.modules["platform"]
    assert callable(getattr(mod, "system", None))
    assert getattr(mod, "__file__", None) is not None
