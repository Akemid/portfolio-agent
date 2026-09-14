"""Unit tests for `DynamoRateLimiter` (design.md SS4.2, `rate-limiting` spec).

Runs against a moto-mocked DynamoDB table (`dynamodb_table` fixture,
`tests/conftest.py`) — no network calls, no real AWS credentials. Counters
are pre-seeded directly against the table to set up "N requests already
made" preconditions without looping the adapter N times.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fakes.frozen_clock import FrozenClock

from api.adapters.dynamo_keys import (
    PARTITION_KEY,
    SORT_KEY,
    TTL_ATTRIBUTE,
    ip_minute_sk,
    ip_pk,
    session_day_sk,
    session_pk,
)
from api.adapters.dynamo_rate_limiter import DynamoRateLimiter

SESSION_KEY = "session-hash-aaaa"
OTHER_SESSION_KEY = "session-hash-bbbb"
IP_KEY = "ip-hash-1111"
OTHER_IP_KEY = "ip-hash-2222"

# A time comfortably inside its minute (not at second=0) so per-minute-window
# math in the IP tests has a non-degenerate elapsed fraction.
NOW = datetime(2026, 9, 7, 14, 32, 10, tzinfo=UTC)


class _CountingTable:
    """Wraps a real (moto-backed) `Table` and counts `update_item` calls per key.

    Used to assert atomicity: exactly one `update_item` call per scope per
    `check_and_increment` invocation — no read-then-write race.
    """

    def __init__(self, table: Any) -> None:
        self._table = table
        self.update_item_calls: list[dict[str, Any]] = []
        self.get_item_calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    def update_item(self, **kwargs: Any) -> Any:
        self.update_item_calls.append(kwargs)
        return self._table.update_item(**kwargs)

    def get_item(self, **kwargs: Any) -> Any:
        self.get_item_calls.append(kwargs)
        return self._table.get_item(**kwargs)

    def query(self, **kwargs: Any) -> Any:
        self.query_calls.append(kwargs)
        return self._table.query(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._table, name)


def _seed_session_count(table: Any, session_key: str, day: str, count: int) -> None:
    table.put_item(
        Item={
            PARTITION_KEY: session_pk(session_key),
            SORT_KEY: session_day_sk(datetime.fromisoformat(day).date()),
            "count": count,
            TTL_ATTRIBUTE: 9999999999,
        }
    )


def _seed_ip_count(table: Any, ip_key: str, minute: datetime, count: int) -> None:
    table.put_item(
        Item={
            PARTITION_KEY: ip_pk(ip_key),
            SORT_KEY: ip_minute_sk(minute),
            "count": count,
            TTL_ATTRIBUTE: 9999999999,
        }
    )


def _get_session_count(table: Any, session_key: str, day: str) -> int:
    item = table.get_item(
        Key={PARTITION_KEY: session_pk(session_key), SORT_KEY: session_day_sk(datetime.fromisoformat(day).date())}
    )["Item"]
    return int(item["count"])


def _get_ip_count(table: Any, ip_key: str, minute: datetime) -> int:
    item = table.get_item(Key={PARTITION_KEY: ip_pk(ip_key), SORT_KEY: ip_minute_sk(minute)})["Item"]
    return int(item["count"])


def test_session_cap_allows_9th_and_increments_to_10(dynamodb_table: Any) -> None:
    _seed_session_count(dynamodb_table, SESSION_KEY, "2026-09-07", count=9)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert decision.allowed is True
    assert _get_session_count(dynamodb_table, SESSION_KEY, "2026-09-07") == 10


def test_session_cap_rejects_11th_without_incrementing(dynamodb_table: Any) -> None:
    _seed_session_count(dynamodb_table, SESSION_KEY, "2026-09-07", count=10)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert decision.allowed is False
    assert decision.scope == "session"
    # Exact seconds from 14:32:10 UTC to the next 00:00 UTC.
    expected_seconds = int((datetime(2026, 9, 8, tzinfo=UTC) - NOW).total_seconds())
    assert decision.retry_after_seconds == expected_seconds
    assert _get_session_count(dynamodb_table, SESSION_KEY, "2026-09-07") == 10


def test_session_windows_isolate_by_calendar_day(dynamodb_table: Any) -> None:
    _seed_session_count(dynamodb_table, SESSION_KEY, "2026-09-06", count=10)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert decision.allowed is True
    assert _get_session_count(dynamodb_table, SESSION_KEY, "2026-09-07") == 1


def test_session_windows_isolate_by_session_key(dynamodb_table: Any) -> None:
    _seed_session_count(dynamodb_table, SESSION_KEY, "2026-09-07", count=10)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(OTHER_SESSION_KEY, IP_KEY)

    assert decision.allowed is True
    assert _get_session_count(dynamodb_table, OTHER_SESSION_KEY, "2026-09-07") == 1


def test_session_ttl_is_end_of_day_plus_grace_and_set_only_once(dynamodb_table: Any) -> None:
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    limiter.check_and_increment(SESSION_KEY, IP_KEY)
    item = dynamodb_table.get_item(Key={PARTITION_KEY: session_pk(SESSION_KEY), SORT_KEY: session_day_sk(NOW.date())})[
        "Item"
    ]
    expected_ttl = int(datetime(2026, 9, 8, tzinfo=UTC).timestamp()) + 300
    assert item[TTL_ATTRIBUTE] == expected_ttl

    later = FrozenClock(datetime(2026, 9, 7, 23, 59, tzinfo=UTC))
    limiter_later = DynamoRateLimiter(table=dynamodb_table, clock=later)
    limiter_later.check_and_increment(SESSION_KEY, IP_KEY)
    item_after_second_write = dynamodb_table.get_item(
        Key={PARTITION_KEY: session_pk(SESSION_KEY), SORT_KEY: session_day_sk(NOW.date())}
    )["Item"]
    assert item_after_second_write[TTL_ATTRIBUTE] == expected_ttl


def test_ip_cap_allows_4th_request_in_window(dynamodb_table: Any) -> None:
    curr_minute = NOW.replace(second=0, microsecond=0)
    _seed_ip_count(dynamodb_table, IP_KEY, curr_minute, count=4)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert decision.allowed is True
    assert _get_ip_count(dynamodb_table, IP_KEY, curr_minute) == 5


def test_ip_cap_rejects_6th_request_without_incrementing(dynamodb_table: Any) -> None:
    curr_minute = NOW.replace(second=0, microsecond=0)
    _seed_ip_count(dynamodb_table, IP_KEY, curr_minute, count=5)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert decision.allowed is False
    assert decision.scope == "ip"
    assert decision.retry_after_seconds == 50  # 60 - 10 seconds elapsed in the minute
    assert _get_ip_count(dynamodb_table, IP_KEY, curr_minute) == 5


def test_ip_windows_isolate_by_ip_key(dynamodb_table: Any) -> None:
    curr_minute = NOW.replace(second=0, microsecond=0)
    _seed_ip_count(dynamodb_table, IP_KEY, curr_minute, count=5)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(SESSION_KEY, OTHER_IP_KEY)

    assert decision.allowed is True
    assert _get_ip_count(dynamodb_table, OTHER_IP_KEY, curr_minute) == 1


def test_ip_ttl_is_bucket_start_plus_grace(dynamodb_table: Any) -> None:
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    limiter.check_and_increment(SESSION_KEY, IP_KEY)

    curr_minute = NOW.replace(second=0, microsecond=0)
    item = dynamodb_table.get_item(Key={PARTITION_KEY: ip_pk(IP_KEY), SORT_KEY: ip_minute_sk(curr_minute)})["Item"]
    expected_ttl = int(curr_minute.timestamp()) + 180
    assert item[TTL_ATTRIBUTE] == expected_ttl


def test_fixed_minute_boundary_burst_is_still_blocked(dynamodb_table: Any) -> None:
    """Regression for the rejected fixed-window design (design.md SS4.2): a plain
    fixed-minute bucket would grant a fresh 5 requests the instant the minute
    rolls over, allowing 10 in a real 60-second span. The weighted window MUST
    NOT permit that: at most one extra request leaks through right after the
    boundary, then the burst is blocked.
    """
    prev_minute = datetime(2026, 9, 7, 14, 31, tzinfo=UTC)
    curr_minute = datetime(2026, 9, 7, 14, 32, tzinfo=UTC)
    _seed_ip_count(dynamodb_table, IP_KEY, prev_minute, count=5)
    just_after_boundary = FrozenClock(curr_minute.replace(second=1))
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=just_after_boundary)

    first = limiter.check_and_increment(SESSION_KEY, IP_KEY)
    second = limiter.check_and_increment(OTHER_SESSION_KEY, IP_KEY)

    assert first.allowed is True
    assert second.allowed is False
    assert second.scope == "ip"


def test_ip_denial_does_not_increment_the_session_counter(dynamodb_table: Any) -> None:
    curr_minute = NOW.replace(second=0, microsecond=0)
    _seed_ip_count(dynamodb_table, IP_KEY, curr_minute, count=5)
    limiter = DynamoRateLimiter(table=dynamodb_table, clock=FrozenClock(NOW))

    decision = limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert decision.allowed is False
    assert decision.scope == "ip"
    item = dynamodb_table.get_item(
        Key={PARTITION_KEY: session_pk(SESSION_KEY), SORT_KEY: session_day_sk(NOW.date())}
    ).get("Item")
    assert item is None


def test_check_and_increment_issues_exactly_one_update_item_call_per_scope(dynamodb_table: Any) -> None:
    counting_table = _CountingTable(dynamodb_table)
    limiter = DynamoRateLimiter(table=counting_table, clock=FrozenClock(NOW))

    limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert len(counting_table.update_item_calls) == 2  # one for IP, one for session


def test_ip_window_reads_only_the_previous_bucket_with_get_item(dynamodb_table: Any) -> None:
    """A point read on the exact previous-minute key is cheaper than a Query and needs no Query IAM permission."""
    counting_table = _CountingTable(dynamodb_table)
    limiter = DynamoRateLimiter(table=counting_table, clock=FrozenClock(NOW))
    prev_minute = NOW.replace(second=0, microsecond=0).replace(minute=NOW.minute - 1)

    limiter.check_and_increment(SESSION_KEY, IP_KEY)

    assert counting_table.query_calls == []
    assert len(counting_table.get_item_calls) == 1
    read = counting_table.get_item_calls[0]
    assert read["Key"] == {PARTITION_KEY: ip_pk(IP_KEY), SORT_KEY: ip_minute_sk(prev_minute)}
    assert read["ConsistentRead"] is True


@pytest.mark.parametrize(("session_key", "ip_key"), [("", IP_KEY), (SESSION_KEY, "")])
def test_empty_derived_keys_are_rejected_before_touching_the_table(
    dynamodb_table: Any, session_key: str, ip_key: str
) -> None:
    """An empty key would merge unrelated visitors into one shared counter; fail loudly instead."""
    counting_table = _CountingTable(dynamodb_table)
    limiter = DynamoRateLimiter(table=counting_table, clock=FrozenClock(NOW))

    with pytest.raises(ValueError):
        limiter.check_and_increment(session_key, ip_key)

    assert counting_table.update_item_calls == []
    assert counting_table.get_item_calls == []
