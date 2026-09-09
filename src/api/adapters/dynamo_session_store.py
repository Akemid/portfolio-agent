"""DynamoDB-backed `SessionStore` adapter (design.md SS4.2, `session-identity` spec).

Only the SHA-256 hash of the session id is ever written to DynamoDB
(design.md SS4.2: "No message text, no raw IP, no raw session id, no name or
email is ever written."). `get()` therefore cannot reconstruct the raw id
from storage — see its docstring for the resulting, deliberate deviation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.domain.hashing import derive_key
from api.domain.models import Session
from api.domain.session_identity import SESSION_TTL
from api.ports.clock import Clock
from api.ports.ids import Ids

_SORT_KEY = "META"


class DynamoSessionStore:
    """Reads and creates session records. Never persists the raw session id.

    `table` is a boto3 DynamoDB `Table` resource, injected by the
    composition root — never created here (design.md SS4, hexagonal
    boundary: adapters depend on ports, not the other way around).
    """

    def __init__(self, table: Any, clock: Clock, ids: Ids) -> None:
        self._table = table
        self._clock = clock
        self._ids = ids

    def get(self, hashed_id: str) -> Session | None:
        """Return the session for `hashed_id`, or `None` if unknown.

        Deviation: the returned `Session.session_id` is the HASHED id (the
        same value passed in as `hashed_id`), not the raw cookie value —
        the raw id is never stored, so it cannot be returned here. This is
        safe because no caller reads `session_id` off a *fetched* session:
        the HTTP layer already holds the raw cookie value it looked up
        with, and a reused session never needs a fresh `Set-Cookie`.
        `session_id` only carries the real, usable value on the `Session`
        returned by `create()`.
        """
        item = self._table.get_item(Key={"pk": f"SESSION#{hashed_id}", "sk": _SORT_KEY}).get("Item")
        if item is None:
            return None
        return Session(session_id=hashed_id, issued_at=datetime.fromisoformat(item["created_at"]))

    def create(self) -> Session:
        """Issue and persist a brand-new session record."""
        raw_id = self._ids.new_session_id()
        issued_at = self._clock.now()
        hashed_id = derive_key("db", raw_id)
        ttl = int((issued_at + SESSION_TTL).timestamp())
        self._table.put_item(
            Item={
                "pk": f"SESSION#{hashed_id}",
                "sk": _SORT_KEY,
                "created_at": issued_at.isoformat(),
                "ttl": ttl,
            }
        )
        return Session(session_id=raw_id, issued_at=issued_at)
