"""Unit tests for `api.observability` (`chat-endpoint` spec, *Log Redaction*)."""

from __future__ import annotations

from api.observability import hash_for_log, truncate


def test_hash_for_log_never_equals_raw_id() -> None:
    raw_id = "super-secret-session-id"

    assert hash_for_log(raw_id) != raw_id


def test_hash_for_log_is_deterministic_for_same_id() -> None:
    raw_id = "super-secret-session-id"

    assert hash_for_log(raw_id) == hash_for_log(raw_id)


def test_hash_for_log_differs_for_different_ids() -> None:
    assert hash_for_log("session-a") != hash_for_log("session-b")


def test_hash_for_log_is_capped_at_16_characters() -> None:
    assert len(hash_for_log("any-session-id")) == 16


def test_truncate_caps_at_100_chars() -> None:
    message = "a" * 300

    assert len(truncate(message, 100)) == 100


def test_truncate_returns_message_unchanged_when_under_cap() -> None:
    message = "short message"

    assert truncate(message, 100) == message


def test_truncate_default_cap_is_100() -> None:
    message = "b" * 150

    assert len(truncate(message)) == 100
