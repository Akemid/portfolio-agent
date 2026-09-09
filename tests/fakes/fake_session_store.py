"""In-memory `SessionStore` test double. No AWS calls, no real persistence."""

from __future__ import annotations

import secrets

from api.domain.hashing import derive_key
from api.domain.models import Session, SessionRecord
from api.ports.clock import Clock
from api.ports.ids import Ids


class _RandomIds:
    """Default `Ids` implementation used when the test does not need determinism."""

    def new_session_id(self) -> str:
        return secrets.token_urlsafe(32)


class FakeSessionStore:
    """In-memory `SessionStore`, keyed the same way as the real DynamoDB adapter."""

    def __init__(self, clock: Clock, ids: Ids | None = None) -> None:
        self._clock = clock
        self._ids = ids or _RandomIds()
        self.records: dict[str, SessionRecord] = {}

    def get(self, hashed_id: str) -> SessionRecord | None:
        return self.records.get(hashed_id)

    def create(self) -> Session:
        session = Session(session_id=self._ids.new_session_id(), issued_at=self._clock.now())
        self.records[derive_key("db", session.session_id)] = SessionRecord(issued_at=session.issued_at)
        return session
