"""Unit tests for `SystemClock` (`api.ports.clock.Clock`)."""

from __future__ import annotations

from datetime import UTC, datetime

from api.adapters.system_clock import SystemClock


def test_now_returns_a_timezone_aware_instant() -> None:
    clock = SystemClock()

    result = clock.now()

    assert result.tzinfo is not None


def test_now_returns_utc() -> None:
    clock = SystemClock()

    result = clock.now()

    assert result.tzinfo == UTC


def test_now_is_close_to_the_real_wall_clock() -> None:
    clock = SystemClock()

    before = datetime.now(UTC)
    result = clock.now()
    after = datetime.now(UTC)

    assert before <= result <= after
