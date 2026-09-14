"""Domain-separated hashing (design.md §4.1). No salt: the session id already
carries >=128 bits of entropy, so there is no secret to store, rotate, or leak.
"""

from __future__ import annotations

import hashlib


def derive_key(prefix: str, value: str) -> str:
    """Return `sha256(f"{prefix}:{value}").hexdigest()`.

    Callers pick the prefix per design.md §4.1 (`db`, `rt`, `log`, `ip`) so the
    same raw value never produces the same key across two different uses.
    """
    return hashlib.sha256(f"{prefix}:{value}".encode()).hexdigest()
