"""Frozen domain value objects for the chat use case (design.md §4). No I/O, no AWS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from api.domain.errors import ValidationError

MAX_MESSAGE_LENGTH = 500
SUPPORTED_LANGUAGES = ("en", "es")

Language = Literal["en", "es"]


@dataclass(frozen=True)
class ChatRequest:
    """A validated, trimmed chat message (`chat-endpoint` spec, *Request Contract*)."""

    message: str

    def __post_init__(self) -> None:
        trimmed = self.message.strip()
        if not trimmed:
            raise ValidationError("message must not be empty")
        if len(trimmed) > MAX_MESSAGE_LENGTH:
            raise ValidationError(f"message must be at most {MAX_MESSAGE_LENGTH} characters")
        object.__setattr__(self, "message", trimmed)


@dataclass(frozen=True)
class AgentAnswer:
    """The agent's response, ready to be serialized as `{"answer", "language"}`."""

    answer: str
    language: str

    def __post_init__(self) -> None:
        if self.language not in SUPPORTED_LANGUAGES:
            raise ValidationError(f"unsupported language: {self.language!r}")


@dataclass(frozen=True)
class Session:
    """An issued session record (`session-identity` spec, *No PII in Session Records*).

    The session id is a bearer credential, so it is excluded from ``repr``/``str`` to keep it
    out of logs and tracebacks (`chat-endpoint` spec, *Log Redaction*).
    """

    session_id: str = field(repr=False)
    issued_at: datetime

    def __post_init__(self) -> None:
        if self.issued_at.tzinfo is None:
            raise ValidationError("Session.issued_at must be timezone-aware")


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of a rate-limit check (`rate-limiting` spec)."""

    allowed: bool
    scope: str | None = None
    retry_after_seconds: int | None = None
