"""Typed domain errors raised by the chat use case.

The HTTP layer (`src/api/http/responder.py`, Phase 4) maps each of these to a
public status code without leaking internal detail (`chat-endpoint` spec,
*Upstream Failure Mapping*).
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every error raised by the domain layer."""


class ValidationError(DomainError):
    """Raised when a client-supplied value fails domain validation."""


class RateLimited(DomainError):
    """Raised when a rate limit has been exceeded.

    Attributes:
        scope: Which limit was hit — `"session"` or `"ip"`.
        retry_after: Seconds until the caller may retry.
    """

    def __init__(self, scope: str, retry_after: int) -> None:
        super().__init__(f"rate limit exceeded for scope={scope!r}, retry_after={retry_after}s")
        self.scope = scope
        self.retry_after = retry_after


class UpstreamError(DomainError):
    """Raised when the agent invocation fails with a non-timeout error."""


class UpstreamTimeout(DomainError):
    """Raised when the agent invocation exceeds the configured timeout budget."""
