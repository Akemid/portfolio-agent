"""Production `Clock` adapter (`api.ports.clock.Clock`) — the real wall clock."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Returns the current, timezone-aware instant."""

    def now(self) -> datetime:
        return datetime.now(UTC)
