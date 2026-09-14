"""Unit tests for `api.domain.hashing` (design.md §4.1, Identity derivation)."""

from api.domain.hashing import derive_key


def test_derive_key_is_domain_separated() -> None:
    assert derive_key("db", "x") != derive_key("rt", "x")
    assert derive_key("db", "x") != derive_key("ip", "x")


def test_derive_key_is_deterministic() -> None:
    assert derive_key("log", "session-1") == derive_key("log", "session-1")


def test_full_sha256_is_64_hex_chars() -> None:
    digest = derive_key("db", "session-1")

    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)
