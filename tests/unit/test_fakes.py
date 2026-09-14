"""Behavioral smoke tests for the in-memory port fakes (design.md §10, *Unit — use case*).

Each test drives a fake through its happy path and asserts a specific,
production-code-derived outcome — proving the fakes behave like the ports
they stand in for before Phase 4/5 use cases start depending on them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes.fake_agent_client import FakeAgentClient
from fakes.fake_rate_limiter import FakeRateLimiter
from fakes.fake_session_store import FakeSessionStore
from fakes.frozen_clock import FrozenClock

from api.domain.errors import UpstreamError
from api.domain.hashing import derive_key
from api.domain.models import AgentAnswer, RateLimitDecision, SessionRecord

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def test_frozen_clock_returns_the_fixed_instant_on_every_call() -> None:
    clock = FrozenClock(NOW)

    assert clock.now() == NOW
    assert clock.now() == NOW


def test_fake_session_store_creates_and_retrieves_by_hashed_id() -> None:
    store = FakeSessionStore(clock=FrozenClock(NOW))

    session = store.create()

    assert store.get(derive_key("db", session.session_id)) == SessionRecord(issued_at=session.issued_at)
    assert store.get("some-other-hash-not-in-the-store") is None


def test_fake_rate_limiter_returns_queued_decisions_then_defaults_to_allowed() -> None:
    limiter = FakeRateLimiter(decisions=[RateLimitDecision(allowed=False, scope="ip", retry_after_seconds=30)])

    first = limiter.check_and_increment("session-key", "ip-key")
    second = limiter.check_and_increment("session-key", "ip-key")

    assert first == RateLimitDecision(allowed=False, scope="ip", retry_after_seconds=30)
    assert second == RateLimitDecision(allowed=True)
    assert limiter.calls == [("session-key", "ip-key"), ("session-key", "ip-key")]


def test_fake_agent_client_returns_the_configured_answer_and_records_the_call() -> None:
    client = FakeAgentClient(answer=AgentAnswer(answer="Hi", language="en"))

    result = client.ask("hello", "runtime-session-id")

    assert result == AgentAnswer(answer="Hi", language="en")
    assert client.calls == [("hello", "runtime-session-id")]


def test_fake_agent_client_raises_the_configured_error_instead_of_answering() -> None:
    client = FakeAgentClient(error=UpstreamError("boom"))

    with pytest.raises(UpstreamError):
        client.ask("hello", "runtime-session-id")
