"""Shared pytest fixtures for the portfolio-agent test suite."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from helpers.dynamodb_schema import create_sessions_table
from moto import mock_aws


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def dynamodb_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A moto-backed DynamoDB table matching design.md SS4.2's schema.

    Dummy credentials and region are set here (never in committed config) so
    boto3 never attempts a real network call or picks up a developer's real
    AWS profile.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    with mock_aws():
        yield create_sessions_table()
