"""In-memory `RateLimiter` test double with a scriptable decision queue."""

from __future__ import annotations

from dataclasses import dataclass, field

from api.domain.models import RateLimitDecision


@dataclass
class FakeRateLimiter:
    """Returns queued decisions in order, defaulting to `allowed=True`."""

    decisions: list[RateLimitDecision] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def check_and_increment(self, session_key: str, ip_key: str) -> RateLimitDecision:
        self.calls.append((session_key, ip_key))
        if self.decisions:
            return self.decisions.pop(0)
        return RateLimitDecision(allowed=True)
