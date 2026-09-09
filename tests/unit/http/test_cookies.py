"""Unit tests for `api.http.cookies` (`session-identity` spec, *Cookie Attributes*)."""

from __future__ import annotations

from api.http.cookies import build_set_cookie_header, parse_session_cookie

SESSION_ID = "a" * 43  # shape of `secrets.token_urlsafe(32)` output


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
