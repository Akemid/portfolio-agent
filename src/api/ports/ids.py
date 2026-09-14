"""Session-id generation port — makes cookie-issuance tests deterministic."""

from __future__ import annotations

from typing import Protocol


class Ids(Protocol):
    """Generates opaque session identifiers."""

    def new_session_id(self) -> str:
        """Return a fresh id with at least 128 bits of entropy (`session-identity` spec)."""
        ...
