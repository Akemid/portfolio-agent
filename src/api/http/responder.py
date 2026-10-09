"""Domain result -> HTTP response (design.md §4 `responder.py`; `chat-endpoint` spec).

Builds an API Gateway HTTP API v2 Lambda-proxy response dict: `statusCode`,
`headers`, `body` (a JSON string). Nothing here calls AWS — Phase 5's `handler.py`
is the only caller, and it returns this dict straight back to API Gateway.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from api.domain.errors import RateLimited, UpstreamError, UpstreamTimeout, ValidationError
from api.http.cookies import build_set_cookie_header
from api.usecases.answer_question import AnswerResult

_ALLOW_ORIGIN = "Access-Control-Allow-Origin"
_ALLOW_CREDENTIALS = "Access-Control-Allow-Credentials"
_VARY = "Vary"


def cors_headers(origin: str | None, allowlist: Sequence[str]) -> dict[str, str]:
    """Return CORS headers for `origin`, restricted to `allowlist`.

    `chat-endpoint` spec, *CORS Restriction*: only an allowlisted origin gets
    `Access-Control-Allow-Origin` and `Access-Control-Allow-Credentials`. `Vary:
    Origin` is always set, because the response content depends on the origin.
    """
    headers = {_VARY: "Origin"}
    if origin is not None and origin in allowlist:
        headers[_ALLOW_ORIGIN] = origin
        headers[_ALLOW_CREDENTIALS] = "true"
    return headers


def build_response(
    status_code: int,
    body: Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return an API Gateway HTTP API v2 Lambda-proxy response dict."""
    response_headers: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        response_headers.update(headers)
    return {"statusCode": status_code, "headers": response_headers, "body": json.dumps(body)}


def success_response(
    result: AnswerResult,
    *,
    origin: str | None,
    allowlist: Sequence[str],
    cookie_secure: bool = True,
) -> dict[str, Any]:
    """Build the `200` response (`chat-endpoint` spec, *Response Contract*).

    Adds `Set-Cookie` only when `decide_session` issued a fresh session
    (`session-identity` spec, *Cookie Issuance*: no `Set-Cookie` on reuse).
    `cookie_secure` defaults to `True`; the composition root sets it from
    `Settings.cookie_secure`, `False` only for unencrypted local development.
    """
    headers = cors_headers(origin, allowlist)
    if result.session_is_new:
        headers["Set-Cookie"] = build_set_cookie_header(result.session.session_id, secure=cookie_secure)
    body = {"answer": result.answer.answer, "language": result.answer.language}
    return build_response(200, body, headers)


def error_response(error: Exception, *, origin: str | None, allowlist: Sequence[str]) -> dict[str, Any]:
    """Map a domain error to a generic, non-leaking error response.

    `chat-endpoint` spec, *Upstream Failure Mapping* and *Rate-Limit Surfacing*:
    the body and headers never carry the exception message, a stack trace, or an
    AWS resource identifier — only a status code, a generic error tag, and (for
    `429`) the rate-limit scope and `Retry-After`. Any error type not raised by
    the chat use case (`ValidationError`, `RateLimited`, `UpstreamError`,
    `UpstreamTimeout`) maps to a generic `500` rather than leaking its detail.
    """
    headers = cors_headers(origin, allowlist)
    if isinstance(error, ValidationError):
        return build_response(400, {"error": "invalid_request"}, headers)
    if isinstance(error, RateLimited):
        headers["Retry-After"] = str(error.retry_after_seconds)
        return build_response(429, {"error": "rate_limited", "scope": error.scope}, headers)
    if isinstance(error, UpstreamTimeout):
        return build_response(504, {"error": "upstream_timeout"}, headers)
    if isinstance(error, UpstreamError):
        return build_response(502, {"error": "upstream_error"}, headers)
    return build_response(500, {"error": "internal_error"}, headers)
