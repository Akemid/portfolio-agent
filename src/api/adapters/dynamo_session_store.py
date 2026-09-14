"""DynamoDB-backed `SessionStore` adapter (design.md SS4.2, `session-identity` spec).

Only the SHA-256 hash of the session id is ever written to DynamoDB
(design.md SS4.2: "No message text, no raw IP, no raw session id, no name or
email is ever written."). `get()` returns a `SessionRecord` — which has no id
field at all — so there is no id to fabricate or leak on a lookup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.adapters.dynamo_keys import META_SK, PARTITION_KEY, SORT_KEY, TTL_ATTRIBUTE, session_pk
from api.domain.hashing import derive_key
from api.domain.models import Session, SessionRecord
from api.domain.session_identity import SESSION_TTL
from api.ports.clock import Clock
from api.ports.ids import Ids


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

    def get(self, hashed_id: str) -> SessionRecord | None:
        """Return the stored record for `hashed_id`, or `None` if unknown.

        Expiry is NOT checked here: this adapter only reports what is (or
        isn't) in the table. Filtering out expired sessions is the domain's
        responsibility (`api.domain.session_identity._is_expired`).

        Uses an eventually-consistent read (no `ConsistentRead=True`): a
        session lookup happens on the request right after the cookie was
        set, never in the same request as the `create()` that wrote it, so
        the replication window is not a practical concern here, and the
        default read is half the RCU cost.
        """
        item = self._table.get_item(Key={PARTITION_KEY: session_pk(hashed_id), SORT_KEY: META_SK}).get("Item")
        if item is None:
            return None
        return SessionRecord(issued_at=datetime.fromisoformat(item["created_at"]))

    def create(self) -> Session:
        """Issue and persist a brand-new session record.

        Returns the full `Session`, including the raw `session_id`: the
        caller needs it once, to send back as `Set-Cookie`.
        """
        raw_id = self._ids.new_session_id()
        issued_at = self._clock.now()
        hashed_id = derive_key("db", raw_id)
        ttl = int((issued_at + SESSION_TTL).timestamp())
        self._table.put_item(
            Item={
                PARTITION_KEY: session_pk(hashed_id),
                SORT_KEY: META_SK,
                "created_at": issued_at.isoformat(),
                TTL_ATTRIBUTE: ttl,
            }
        )
        return Session(session_id=raw_id, issued_at=issued_at)
