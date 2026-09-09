"""Smoke test exercising a real `src` module for the coverage gate."""

import api


def test_api_package_exposes_version() -> None:
    """The `api` package MUST expose a non-empty `__version__` string.

    This keeps `uv run pytest --cov=src --cov-fail-under=85` measuring real
    statements instead of reporting a vacuous 0/0 pass on an empty package.
    """
    assert isinstance(api.__version__, str)
    assert api.__version__
