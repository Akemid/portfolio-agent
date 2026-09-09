"""Production `Ids` adapter (`api.ports.ids.Ids`) — cryptographically random session ids."""

from __future__ import annotations

import secrets

# 32 bytes = 256 bits, comfortably above the >= 128 bit floor required by the
# `session-identity` spec (*Session Identifier Quality*). Matches the fake
# used in tests (`tests/fakes/fake_session_store.py`).
_ENTROPY_BYTES = 32


class SecureIds:
    """Generates opaque session identifiers from a CSPRNG."""

    def new_session_id(self) -> str:
        return secrets.token_urlsafe(_ENTROPY_BYTES)
