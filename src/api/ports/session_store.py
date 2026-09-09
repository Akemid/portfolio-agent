"""Session persistence port (design.md §4 — Lambda Module Design)."""

from __future__ import annotations

from typing import Protocol

from api.domain.models import Session


class SessionStore(Protocol):
    """Reads and creates session records. MUST NOT persist PII (`session-identity` spec)."""

    def get(self, hashed_id: str) -> Session | None:
        """Return the session for `hashed_id`, or `None` if unknown or expired."""
        ...

    def create(self) -> Session:
        """Issue and persist a brand-new session record."""
        ...
