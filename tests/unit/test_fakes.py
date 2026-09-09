"""Smoke tests for the in-memory port fakes under `tests/fakes/`.

These are test infrastructure (task 2.5 has no dedicated RED test), but a
light check here catches wiring mistakes before Phase 4/2b tests depend on
them.
"""

from datetime import UTC, datetime

from fakes.fake_agent_client import FakeAgentClient
from fakes.fake_rate_limiter import FakeRateLimiter
from fakes.fake_session_store import FakeSessionStore
from fakes.frozen_clock import FrozenClock

from api.domain.hashing import derive_key
from api.domain.models import AgentAnswer, RateLimitDecision


def test_frozen_clock_returns_fixed_instant() -> None:
    fixed = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    assert FrozenClock(fixed_now=fixed).now() == fixed


def test_fake_session_store_create_then_get_round_trips() -> None:
    store = FakeSessionStore(clock=FrozenClock(fixed_now=datetime(2026, 9, 7, 12, 0, tzinfo=UTC)))

    session = store.create()

    assert store.get(derive_key("db", session.session_id)) == session
    assert store.get("unknown-hash") is None


def test_fake_rate_limiter_replays_queued_decisions_then_defaults_to_allowed() -> None:
    limiter = FakeRateLimiter(decisions=[RateLimitDecision(allowed=False, scope="ip", retry_after_seconds=5)])

    first = limiter.check_and_increment("session-key", "ip-key")
    second = limiter.check_and_increment("session-key", "ip-key")

    assert first == RateLimitDecision(allowed=False, scope="ip", retry_after_seconds=5)
    assert second == RateLimitDecision(allowed=True)
    assert limiter.calls == [("session-key", "ip-key"), ("session-key", "ip-key")]


def test_fake_agent_client_raises_scripted_error() -> None:
    client = FakeAgentClient(error=ValueError("boom"))

    try:
        client.ask("hello", "runtime-session-id")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError to propagate")

    assert client.calls == [("hello", "runtime-session-id")]


def test_fake_agent_client_returns_configured_answer() -> None:
    answer = AgentAnswer(answer="Hola.", language="es")
    client = FakeAgentClient(answer=answer)

    assert client.ask("hola?", "rt-id") == answer
