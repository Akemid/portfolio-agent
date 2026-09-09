"""Unit tests for `SecureIds` (`session-identity` spec, *Session Identifier Quality*)."""

from __future__ import annotations

from api.adapters.secure_ids import SecureIds
from api.domain.session_identity import is_valid_session_id_shape


def test_new_session_id_matches_the_expected_shape() -> None:
    ids = SecureIds()

    session_id = ids.new_session_id()

    assert is_valid_session_id_shape(session_id)


def test_new_session_id_has_at_least_128_bits_of_entropy() -> None:
    ids = SecureIds()

    session_id = ids.new_session_id()

    # base64url with no padding: 4 chars per 3 bytes, so >= 22 chars implies >= 16 bytes (128 bits).
    assert len(session_id) >= 22


def test_new_session_id_is_random_across_calls() -> None:
    ids = SecureIds()

    first = ids.new_session_id()
    second = ids.new_session_id()

    assert first != second
