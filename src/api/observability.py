"""Log-redaction helpers (`chat-endpoint` spec, *Log Redaction*; design.md §9.3).

Pure functions only — no logging I/O. The composition root (Phase 5's
`handler.py`) calls these before emitting any log line, so a raw session id or
a full message body never reaches CloudWatch.
"""

from __future__ import annotations

from api.domain.hashing import derive_key

_LOG_HASH_LENGTH = 16
_DEFAULT_MESSAGE_CAP = 100


def hash_for_log(session_id: str) -> str:
    """Return a short, one-way correlation id for `session_id`, safe to log.

    Uses the `"log"` domain-separation prefix (design.md §4.1) so this hash can
    never be replayed as a DynamoDB or AgentCore key even if it leaked.
    """
    return derive_key("log", session_id)[:_LOG_HASH_LENGTH]


def truncate(message: str, max_length: int = _DEFAULT_MESSAGE_CAP) -> str:
    """Return `message` capped at `max_length` characters (`chat-endpoint` spec,
    *Log Redaction*: at most a 100-character prefix, never the full body).
    """
    return message[:max_length]
