"""Session identity domain logic (`session-identity` spec).

Resolves the effective session for an incoming request: reuse a valid,
unexpired session referenced by the request's cookie, or issue a fresh one.
Unknown, expired, and malformed cookie values are all treated identically —
the caller is never rejected because of an invalid `session_id` cookie
(`session-identity` spec, *Tamper and Unknown-ID Handling*).
"""

from __future__ import annotations

import re
from datetime import timedelta

from api.domain.hashing import derive_key
from api.domain.models import Session
from api.ports.clock import Clock
from api.ports.session_store import SessionStore

SESSION_TTL = timedelta(hours=24)

# `secrets.token_urlsafe` output: URL-safe base64 alphabet, no padding. 128
# bits of entropy needs ~22 chars at 6 bits/char, so a well-formed id is
# always accepted while cookie-injected garbage never reaches the store.
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,128}$")


def is_valid_session_id_shape(value: str) -> bool:
    """Return whether `value` has the charset/length of a real session id."""
    return bool(SESSION_ID_PATTERN.match(value))


def decide_session(
    cookie_value: str | None,
    store: SessionStore,
    clock: Clock,
) -> tuple[Session, bool]:
    """Return the effective `Session` and whether it was just created.

    A `True` second element means the caller MUST send a fresh `Set-Cookie`
    header (`session-identity` spec, *Cookie Issuance*).
    """
    if cookie_value is not None and is_valid_session_id_shape(cookie_value):
        existing = store.get(derive_key("db", cookie_value))
        if existing is not None and not _is_expired(existing, clock):
            return existing, False

    return store.create(), True


def _is_expired(session: Session, clock: Clock) -> bool:
    return clock.now() >= session.issued_at + SESSION_TTL
