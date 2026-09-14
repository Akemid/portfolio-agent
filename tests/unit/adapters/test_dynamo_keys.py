"""Unit tests for the DynamoDB key-shape helpers (design.md SS4.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from api.adapters.dynamo_keys import ip_minute_sk, ip_pk, session_day_sk


def test_ip_pk_prefixes_the_hashed_ip() -> None:
    assert ip_pk("abc123") == "IP#abc123"


def test_session_day_sk_formats_the_utc_calendar_date() -> None:
    assert session_day_sk(date(2026, 9, 7)) == "DAY#2026-09-07"


def test_ip_minute_sk_formats_the_utc_minute_bucket() -> None:
    assert ip_minute_sk(datetime(2026, 9, 7, 14, 32, tzinfo=UTC)) == "MIN#2026-09-07T14:32"


def test_ip_minute_sk_truncates_seconds_within_the_minute() -> None:
    """Two instants in the same minute MUST map to the same bucket key."""
    early = ip_minute_sk(datetime(2026, 9, 7, 14, 32, 1, tzinfo=UTC))
    late = ip_minute_sk(datetime(2026, 9, 7, 14, 32, 59, tzinfo=UTC))

    assert early == late == "MIN#2026-09-07T14:32"
