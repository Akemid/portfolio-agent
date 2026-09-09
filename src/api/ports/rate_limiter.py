"""Rate-limit checking port (`rate-limiting` spec)."""

from __future__ import annotations

from typing import Protocol

from api.domain.models import RateLimitDecision


class RateLimiter(Protocol):
    """Checks and atomically increments both the session and IP counters.

    Implementations MUST evaluate and increment before the caller invokes the
    agent (`rate-limiting` spec, *Check-and-Increment Before Invocation*).
    """

    def check_and_increment(self, session_key: str, ip_key: str) -> RateLimitDecision:
        """Return whether the request is allowed under both rate limits."""
        ...
