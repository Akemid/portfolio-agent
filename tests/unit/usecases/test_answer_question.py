"""Unit tests for `api.usecases.answer_question` (design.md §4, `rate-limiting` spec)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes.fake_agent_client import FakeAgentClient
from fakes.fake_rate_limiter import FakeRateLimiter
from fakes.fake_session_store import FakeSessionStore
from fakes.frozen_clock import FrozenClock

from api.domain.errors import RateLimited, UpstreamError, ValidationError
from api.domain.hashing import derive_key
from api.domain.models import AgentAnswer, RateLimitDecision
from api.ports.clock import Clock
from api.usecases.answer_question import answer_question

ISSUED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SOURCE_IP = "203.0.113.5"


class _OrderTrackingSessionStore(FakeSessionStore):
    """Records which of its methods ran, in call order, into a shared list."""

    def __init__(self, order: list[str], clock: Clock) -> None:
        super().__init__(clock)
        self._order = order

    def get(self, hashed_id: str) -> object:
        self._order.append("session_store.get")
        return super().get(hashed_id)

    def create(self) -> object:
        self._order.append("session_store.create")
        return super().create()


class _OrderTrackingRateLimiter(FakeRateLimiter):
    def __init__(self, order: list[str], decisions: list[RateLimitDecision]) -> None:
        super().__init__(decisions=decisions)
        self._order = order

    def check_and_increment(self, session_key: str, ip_key: str) -> RateLimitDecision:
        self._order.append("rate_limiter")
        return super().check_and_increment(session_key, ip_key)


class _OrderTrackingAgentClient(FakeAgentClient):
    def __init__(self, order: list[str], answer: AgentAnswer) -> None:
        super().__init__(answer=answer)
        self._order = order

    def ask(self, prompt: str, runtime_session_id: str) -> AgentAnswer:
        self._order.append("agent_client")
        return super().ask(prompt, runtime_session_id)


def test_happy_path_calls_ports_in_order() -> None:
    order: list[str] = []
    clock = FrozenClock(ISSUED_AT)
    store = _OrderTrackingSessionStore(order, clock)
    limiter = _OrderTrackingRateLimiter(order, [])
    agent = _OrderTrackingAgentClient(order, AgentAnswer(answer="Hi there.", language="en"))

    result = answer_question(
        "hello",
        None,
        SOURCE_IP,
        session_store=store,
        rate_limiter=limiter,
        agent_client=agent,
        clock=clock,
    )

    assert order == ["session_store.create", "rate_limiter", "agent_client"]
    assert result.session_is_new is True
    assert result.answer.answer == "Hi there."


def test_rate_limit_short_circuits_before_invoke() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock)
    limiter = FakeRateLimiter(decisions=[RateLimitDecision(allowed=False, scope="ip", retry_after_seconds=30)])
    agent = FakeAgentClient()

    with pytest.raises(RateLimited) as exc_info:
        answer_question(
            "hello",
            None,
            SOURCE_IP,
            session_store=store,
            rate_limiter=limiter,
            agent_client=agent,
            clock=clock,
        )

    assert exc_info.value.scope == "ip"
    assert exc_info.value.retry_after_seconds == 30
    assert agent.calls == []


def test_upstream_error_maps_to_upstream_error_domain_type() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock)
    limiter = FakeRateLimiter()
    agent = FakeAgentClient(error=UpstreamError("boom"))

    with pytest.raises(UpstreamError):
        answer_question(
            "hello",
            None,
            SOURCE_IP,
            session_store=store,
            rate_limiter=limiter,
            agent_client=agent,
            clock=clock,
        )


def test_invalid_message_short_circuits_before_session_lookup() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock)
    limiter = FakeRateLimiter()
    agent = FakeAgentClient()

    with pytest.raises(ValidationError):
        answer_question(
            "   ",
            None,
            SOURCE_IP,
            session_store=store,
            rate_limiter=limiter,
            agent_client=agent,
            clock=clock,
        )

    assert store.records == {}
    assert limiter.calls == []
    assert agent.calls == []


def test_reused_session_is_not_marked_as_new() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock)
    existing = store.create()
    limiter = FakeRateLimiter()
    agent = FakeAgentClient()

    result = answer_question(
        "hello",
        existing.session_id,
        SOURCE_IP,
        session_store=store,
        rate_limiter=limiter,
        agent_client=agent,
        clock=clock,
    )

    assert result.session_is_new is False
    assert result.session == existing


def test_rate_limiter_receives_derived_session_and_ip_keys() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock)
    limiter = FakeRateLimiter()
    agent = FakeAgentClient()

    result = answer_question(
        "hello",
        None,
        SOURCE_IP,
        session_store=store,
        rate_limiter=limiter,
        agent_client=agent,
        clock=clock,
    )

    assert limiter.calls == [(derive_key("db", result.session.session_id), derive_key("ip", SOURCE_IP))]


def test_denied_decision_without_scope_or_retry_after_raises_value_error() -> None:
    """Defensive guard: a `RateLimiter` port implementation must never deny without both fields."""
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock)
    limiter = FakeRateLimiter(decisions=[RateLimitDecision(allowed=False)])
    agent = FakeAgentClient()

    with pytest.raises(ValueError, match="scope and retry_after_seconds"):
        answer_question(
            "hello",
            None,
            SOURCE_IP,
            session_store=store,
            rate_limiter=limiter,
            agent_client=agent,
            clock=clock,
        )


def test_agent_client_receives_trimmed_message_and_runtime_session_id() -> None:
    clock = FrozenClock(ISSUED_AT)
    store = FakeSessionStore(clock)
    limiter = FakeRateLimiter()
    agent = FakeAgentClient()

    result = answer_question(
        "  hello  ",
        None,
        SOURCE_IP,
        session_store=store,
        rate_limiter=limiter,
        agent_client=agent,
        clock=clock,
    )

    assert agent.calls == [("hello", derive_key("rt", result.session.session_id))]
