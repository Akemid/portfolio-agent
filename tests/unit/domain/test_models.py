"""Unit tests for `api.domain.models` — frozen domain value objects."""

from datetime import UTC, datetime

import pytest

from api.domain.errors import ValidationError
from api.domain.models import AgentAnswer, ChatRequest, RateLimitDecision, Session

MAX_MESSAGE_LENGTH = 500


def test_chat_request_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="")


def test_chat_request_rejects_whitespace_only_message() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="   ")


def test_chat_request_rejects_message_over_500_chars() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="a" * (MAX_MESSAGE_LENGTH + 1))


def test_chat_request_accepts_message_at_exactly_500_chars() -> None:
    request = ChatRequest(message="a" * MAX_MESSAGE_LENGTH)

    assert len(request.message) == MAX_MESSAGE_LENGTH


def test_chat_request_trims_surrounding_whitespace() -> None:
    request = ChatRequest(message="  What is your experience with React?  ")

    assert request.message == "What is your experience with React?"


@pytest.mark.parametrize("language", ["en", "es"])
def test_agent_answer_accepts_supported_languages(language: str) -> None:
    answer = AgentAnswer(answer="Hi there.", language=language)

    assert answer.language == language


def test_agent_answer_rejects_unsupported_language() -> None:
    with pytest.raises(ValidationError):
        AgentAnswer(answer="Hi there.", language="fr")


def test_session_holds_id_and_issued_at() -> None:
    issued_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    session = Session(session_id="abc123", issued_at=issued_at)

    assert session.session_id == "abc123"
    assert session.issued_at == issued_at


@pytest.mark.parametrize(
    ("allowed", "scope", "retry_after_seconds"),
    [
        (True, None, None),
        (False, "session", 3600),
        (False, "ip", 42),
    ],
)
def test_rate_limit_decision_holds_scope_and_retry_after(
    allowed: bool, scope: str | None, retry_after_seconds: int | None
) -> None:
    decision = RateLimitDecision(allowed=allowed, scope=scope, retry_after_seconds=retry_after_seconds)

    assert decision.allowed is allowed
    assert decision.scope == scope
    assert decision.retry_after_seconds == retry_after_seconds
