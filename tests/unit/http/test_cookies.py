"""Unit tests for `api.http.cookies` (`session-identity` spec, *Cookie Attributes*)."""

from __future__ import annotations

from api.domain.session_identity import SESSION_TTL
from api.http.cookies import DEFAULT_MAX_AGE_SECONDS, build_set_cookie_header, parse_session_cookie

SESSION_ID = "a" * 43  # shape of `secrets.token_urlsafe(32)` output


def test_default_max_age_is_derived_from_the_session_ttl() -> None:
    """The cookie lifetime and the server-side TTL MUST come from one constant."""
    assert int(SESSION_TTL.total_seconds()) == DEFAULT_MAX_AGE_SECONDS


def test_cookie_has_all_required_flags() -> None:
    header = build_set_cookie_header(SESSION_ID)

    assert header == f"session_id={SESSION_ID}; Max-Age=86400; Path=/; HttpOnly; Secure; SameSite=Lax"


def test_cookie_max_age_is_configurable() -> None:
    header = build_set_cookie_header(SESSION_ID, max_age_seconds=60)

    assert "Max-Age=60" in header
    assert "Max-Age=86400" not in header


def test_cookie_secure_flag_can_be_disabled_for_local_dev() -> None:
    header = build_set_cookie_header(SESSION_ID, secure=False)

    assert "Secure" not in header
    assert "SameSite=Lax" in header


def test_parse_extracts_session_id_among_other_cookies() -> None:
    header = f"other=1; session_id={SESSION_ID}; theme=dark"

    assert parse_session_cookie(header) == SESSION_ID


def test_parse_returns_none_when_header_is_missing() -> None:
    assert parse_session_cookie(None) is None


def test_parse_returns_none_when_header_is_empty() -> None:
    assert parse_session_cookie("") is None


def test_parse_returns_none_when_session_cookie_is_absent() -> None:
    assert parse_session_cookie("other=1; theme=dark") is None


def test_parse_tolerates_whitespace_around_cookie_pairs() -> None:
    header = f"  other=1 ;  session_id={SESSION_ID}  ; theme=dark"

    assert parse_session_cookie(header) == SESSION_ID


def test_parse_rejects_value_outside_the_session_id_charset() -> None:
    header = "session_id=<script>alert(1)</script>"

    assert parse_session_cookie(header) is None


def test_parse_rejects_duplicate_session_cookies() -> None:
    """Two `session_id` cookies are ambiguous (cookie-injection shape); treat as no cookie."""
    header = f"session_id={SESSION_ID}; theme=dark; session_id={'b' * 43}"

    assert parse_session_cookie(header) is None


def test_parse_rejects_oversized_value() -> None:
    header = f"session_id={'a' * 129}"

    assert parse_session_cookie(header) is None
