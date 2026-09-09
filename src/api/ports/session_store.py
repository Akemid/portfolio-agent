"""Session persistence port (design.md §4 — Lambda Module Design)."""

from __future__ import annotations

from typing import Protocol

from api.domain.models import Session, SessionRecord


class SessionStore(Protocol):
    """Reads and creates session records. MUST NOT persist PII (`session-identity` spec)."""

    def get(self, hashed_id: str) -> SessionRecord | None:
        """Return the stored record for `hashed_id`, or `None` if it is unknown.

        Expiry is NOT this method's concern: it returns whatever is on record,
        expired or not. Filtering out expired sessions is the DOMAIN's
        responsibility (`api.domain.session_identity._is_expired`), not the
        store's — a store adapter has no business hardcoding the TTL policy.

        Returns a `SessionRecord`, not a `Session`: a lookup is keyed by the
        already-known hashed id, so the caller never needs the id echoed
        back, and an adapter that only ever persists the hash (never the raw
        id) has no raw id to put on a `Session.session_id` in the first
        place. Reconstructing the raw-id-bearing `Session` for a reused
        cookie is the domain's job.
        """
        ...

    def create(self) -> Session:
        """Issue and persist a brand-new session record.

        Returns the full `Session`, including the raw `session_id`: the
        caller needs it once, to send back as `Set-Cookie`.
        """
        ...
