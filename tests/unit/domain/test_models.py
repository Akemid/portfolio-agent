"""Unit tests for `api.domain.models` — frozen domain value objects."""

import pytest

from api.domain.errors import ValidationError
from api.domain.models import AgentAnswer, ChatRequest

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


def test_agent_answer_rejects_unsupported_language() -> None:
    with pytest.raises(ValidationError):
        AgentAnswer(answer="Hi there.", language="fr")
