"""Session cookie encoding/decoding for the HTTP layer (`session-identity` spec)."""

from __future__ import annotations

from api.domain.session_identity import SESSION_TTL, is_valid_session_id_shape

SESSION_COOKIE_NAME = "session_id"
# Single source of truth for the fixed 24h lifetime: the cookie expires exactly when the server does.
DEFAULT_MAX_AGE_SECONDS = int(SESSION_TTL.total_seconds())


def build_set_cookie_header(
    session_id: str,
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    secure: bool = True,
) -> str:
    """Build the `Set-Cookie` header value for a session.

    Attribute order is fixed (`session-identity` spec, *Cookie Attributes*):
    the id pair, `Max-Age`, `Path=/`, `HttpOnly`, optional `Secure`, then
    `SameSite=Lax`. `secure=False` exists only for unencrypted local
    development; production callers MUST leave it at the default.
    """
    attributes = [
        f"{SESSION_COOKIE_NAME}={session_id}",
        f"Max-Age={max_age_seconds}",
        "Path=/",
        "HttpOnly",
    ]
    if secure:
        attributes.append("Secure")
    attributes.append("SameSite=Lax")
    return "; ".join(attributes)


def parse_session_cookie(cookie_header: str | None) -> str | None:
    """Extract the `session_id` cookie value from a raw `Cookie` header.

    Returns `None` when the header is absent, the cookie is missing, the
    value does not match the expected id shape, or the cookie appears more
    than once (ambiguous, cookie-injection shape) — callers treat all of
    these the same as "no cookie" and let `decide_session` issue a new one.
    """
    if not cookie_header:
        return None

    candidates = [
        value
        for name, _, value in (part.strip().partition("=") for part in cookie_header.split(";"))
        if name == SESSION_COOKIE_NAME
    ]
    if len(candidates) != 1:
        return None

    value = candidates[0]
    return value if is_valid_session_id_shape(value) else None
