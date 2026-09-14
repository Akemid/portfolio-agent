"""Clock port — makes TTL and session-age tests deterministic."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Returns the current instant."""

    def now(self) -> datetime:
        """Return the current, timezone-aware instant."""
        ...
