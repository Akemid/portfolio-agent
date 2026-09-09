"""Unit tests for `api.domain.session_identity.decide_session` (`session-identity` spec)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fakes.fake_session_store import FakeSessionStore
from fakes.frozen_clock import FrozenClock

from api.domain.errors import ValidationError
from api.domain.hashing import derive_key
from api.domain.models import Session
from api.domain.session_identity import decide_session

ISSUED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class _RefusingStore:
    """A `SessionStore` double that fails the test if `get` is ever called."""

    def __init__(self, created: Session) -> None:
        self._created = created

    def get(self, hashed_id: str) -> Session | None:
        raise AssertionError("decide_session must not query the store for a malformed cookie value")

    def create(self) -> Session:
        return self._created


def test_no_cookie_creates_new_session() -> None:
    store = FakeSessionStore(clock=FrozenClock(ISSUED_AT))

    session, is_new = decide_session(None, store, FrozenClock(ISSUED_AT))

    assert is_new is True
    assert derive_key("db", session.session_id) in store.records


def test_valid_unexpired_cookie_reuses_session_without_new_cookie() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock=clock)
    existing = store.create()

    session, is_new = decide_session(existing.session_id, store, clock)

    assert is_new is False
    assert session == existing


def test_reused_session_carries_the_raw_cookie_id_not_the_hash() -> None:
    """Regression: `SessionStore.get()` returns a `SessionRecord` keyed by the hashed id,
    which carries no session id at all. `decide_session` must reconstruct the reused
    `Session` from the raw `cookie_value` it was called with, never from the store's
    record — so `session.session_id` is always the raw id, matching the `create()` path,
    and never the hash used as the lookup key.
    """
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock=clock)
    existing = store.create()

    session, is_new = decide_session(existing.session_id, store, clock)

    assert is_new is False
    assert session.session_id == existing.session_id
    assert session.session_id != derive_key("db", existing.session_id)


def test_unknown_cookie_silently_issues_fresh_session() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock=clock)
    # Well-formed shape, but never stored: simulates a tampered/unknown id.
    unknown_id = "unknown0but0well0formed0session0id0value00"

    session, is_new = decide_session(unknown_id, store, clock)

    assert is_new is True
    assert session.session_id != unknown_id


def test_malformed_cookie_value_skips_store_lookup_and_issues_fresh_session() -> None:
    clock = FrozenClock(ISSUED_AT)
    fresh = Session(session_id="brand-new-session-id-0000000000000000000", issued_at=ISSUED_AT)
    store = _RefusingStore(created=fresh)

    session, is_new = decide_session("bad value; DROP TABLE", store, clock)

    assert is_new is True
    assert session == fresh


def test_session_25_hours_old_is_treated_as_expired() -> None:
    store = FakeSessionStore(clock=FrozenClock(ISSUED_AT))
    old_session = store.create()
    later_clock = FrozenClock(ISSUED_AT + timedelta(hours=25))

    session, is_new = decide_session(old_session.session_id, store, later_clock)

    assert is_new is True
    assert session.session_id != old_session.session_id


def test_session_10_hours_old_is_still_valid() -> None:
    store = FakeSessionStore(clock=FrozenClock(ISSUED_AT))
    existing = store.create()
    later_clock = FrozenClock(ISSUED_AT + timedelta(hours=10))

    session, is_new = decide_session(existing.session_id, store, later_clock)

    assert is_new is False
    assert session == existing


def test_session_exactly_24_hours_old_is_treated_as_expired() -> None:
    """The TTL is fixed at 24h, not extended by activity: the boundary itself is expired."""
    store = FakeSessionStore(clock=FrozenClock(ISSUED_AT))
    existing = store.create()
    boundary_clock = FrozenClock(ISSUED_AT + timedelta(hours=24))

    session, is_new = decide_session(existing.session_id, store, boundary_clock)

    assert is_new is True
    assert session.session_id != existing.session_id


def test_naive_clock_now_is_rejected_when_reusing_a_session() -> None:
    """A naive `now()` must surface as a domain error, never as a bare TypeError from datetime math."""
    aware_clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock=aware_clock)
    existing = store.create()
    naive_clock = FrozenClock(datetime(2026, 9, 9, 13, 0))  # no tzinfo

    with pytest.raises(ValidationError):
        decide_session(existing.session_id, store, naive_clock)


def test_naive_clock_now_is_rejected_when_creating_a_session() -> None:
    naive_clock = FrozenClock(datetime(2026, 9, 9, 12, 0))  # no tzinfo
    store = FakeSessionStore(clock=naive_clock)

    with pytest.raises(ValidationError):
        decide_session(None, store, naive_clock)
