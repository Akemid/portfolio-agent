"""Domain-separated hashing (design.md §4.1, Identity derivation).

The session id already carries >=128 bits of CSPRNG entropy
(`session-identity` spec, *Session Identifier Quality*), so a plain SHA-256
with a domain-separation prefix is sufficient: no salt, therefore no secret
to store, rotate, or leak.
"""

from __future__ import annotations

import hashlib


def derive_key(prefix: str, value: str) -> str:
    """Return `sha256(f"{prefix}:{value}").hexdigest()`.

    Callers pick the prefix per design.md §4.1 (`db`, `rt`, `log`, `ip`) so the
    same raw value never produces the same key across two different uses —
    a DynamoDB read cannot forge a cookie, and the AgentCore
    `runtimeSessionId` never doubles as the DB partition key.
    """
    return hashlib.sha256(f"{prefix}:{value}".encode()).hexdigest()
