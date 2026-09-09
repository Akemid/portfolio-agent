"""In-memory `Clock` test double."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class FrozenClock:
    """Returns a fixed instant, controlled by the test."""

    fixed_now: datetime

    def now(self) -> datetime:
        return self.fixed_now
