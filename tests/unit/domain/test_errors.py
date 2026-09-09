"""Unit tests for `api.domain.errors`."""

import pytest

from api.domain.errors import DomainError, RateLimited, UpstreamError, UpstreamTimeout, ValidationError


def test_rate_limited_carries_scope_and_retry_after() -> None:
    error = RateLimited(scope="ip", retry_after=42)

    assert error.scope == "ip"
    assert error.retry_after == 42


@pytest.mark.parametrize("error_type", [ValidationError, UpstreamError, UpstreamTimeout])
def test_domain_errors_are_exceptions(error_type: type[Exception]) -> None:
    assert issubclass(error_type, DomainError)
    assert issubclass(error_type, Exception)
