"""Unit tests for `api.domain.models` — frozen domain value objects."""

from datetime import UTC, datetime

import pytest

from api.domain.errors import ValidationError
from api.domain.models import AgentAnswer, ChatRequest, Session

MAX_MESSAGE_LENGTH = 500


def test_chat_request_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="")


def test_chat_request_rejects_message_over_500_chars() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="a" * (MAX_MESSAGE_LENGTH + 1))


def test_chat_request_accepts_message_at_exactly_500_chars() -> None:
    request = ChatRequest(message="a" * MAX_MESSAGE_LENGTH)

    assert len(request.message) == MAX_MESSAGE_LENGTH


def test_chat_request_trims_surrounding_whitespace() -> None:
    assert ChatRequest(message="  hi  ").message == "hi"


def test_chat_request_rejects_whitespace_only_message() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message=" \t\n ")


def test_chat_request_counts_length_in_characters_not_bytes() -> None:
    # Each "ñ" is one character but two UTF-8 bytes: 500 of them must be accepted.
    request = ChatRequest(message="ñ" * MAX_MESSAGE_LENGTH)

    assert len(request.message) == MAX_MESSAGE_LENGTH


def test_session_repr_does_not_expose_session_id() -> None:
    """The session id is a bearer credential; it must never leak through repr/str into logs."""
    session = Session(session_id="super-secret-session-id", issued_at=datetime(2026, 9, 9, tzinfo=UTC))

    assert "super-secret-session-id" not in repr(session)
    assert "super-secret-session-id" not in str(session)


def test_agent_answer_rejects_unsupported_language() -> None:
    with pytest.raises(ValidationError):
        AgentAnswer(answer="Hi there.", language="fr")
