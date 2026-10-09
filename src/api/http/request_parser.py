"""API Gateway HTTP API v2 event -> `IncomingRequest` (design.md §4 `handler.py`
composition root; `chat-endpoint` spec, *Request Contract*; `rate-limiting`
spec, *Header Spoofing Attempt*).

`parse_event` can raise `ValidationError` when `isBase64Encoded` is true and
`event["body"]` is not valid base64, or decodes to bytes that are not valid
UTF-8 — a malformed body must never escape as an unhandled exception (it must
map to a generic `400`, never a raw exception message). Every other field is
unwrapped without raising, so a caller still has `origin`/`cookie_value`
available to build an error response even when the body turns out to be
invalid JSON. `extract_message` is the separate, raising step that turns the
raw body into the validated `message` string (`chat-endpoint` spec, *Invalid
JSON*) — kept apart so a parsing failure still leaves `IncomingRequest` usable
for CORS/error headers.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api.domain.errors import ValidationError
from api.http.cookies import parse_session_cookie


@dataclass(frozen=True)
class IncomingRequest:
    """The AWS event shape, unwrapped into plain fields. Never partially built."""

    method: str
    path: str
    body: str | None
    cookie_value: str | None
    origin: str | None
    source_ip: str | None


def parse_event(event: Mapping[str, Any]) -> IncomingRequest:
    """Extract method, path, body, cookie value, origin, and source IP.

    `source_ip` comes ONLY from `requestContext.http.sourceIp` — never from
    an `X-Forwarded-For` header, which a client fully controls and could
    forge to evade the per-IP rate limit (`rate-limiting` spec). It is
    `None` when missing or blank, so a caller can reject the request instead
    of deriving a rate-limit key from an empty string (`rate-limiting` spec,
    *Header Spoofing Attempt*: an empty key must never collapse unrelated
    visitors into one shared counter — see `DynamoRateLimiter`'s own
    empty-key guard).
    """
    http = event.get("requestContext", {}).get("http", {})
    headers = event.get("headers") or {}
    return IncomingRequest(
        method=http.get("method", ""),
        path=http.get("path", ""),
        body=_decode_body(event),
        cookie_value=_extract_cookie_value(event, headers),
        origin=_header(headers, "origin"),
        source_ip=http.get("sourceIp") or None,
    )


def extract_origin(event: Mapping[str, Any]) -> str | None:
    """Return the `Origin` header from `event`, without decoding the body.

    Never raises. Used by the composition root to build a CORS-correct error
    response even when `parse_event` itself raised (e.g. a malformed base64
    body) — the origin lookup must not depend on whether the body could be
    decoded.
    """
    headers = event.get("headers") or {}
    return _header(headers, "origin")


def extract_message(body: str | None) -> str:
    """Return the validated `message` string from a raw JSON request body.

    Raises `ValidationError` for a missing body, malformed JSON, a
    non-object body, or a missing/non-string `message` field
    (`chat-endpoint` spec, *Request Contract*: "Invalid JSON"). Message
    trimming, emptiness, and the 500-char length cap are NOT checked here —
    that is `ChatRequest`'s job, applied later inside `answer_question`, so
    the rule lives in exactly one place.
    """
    if not body:
        raise ValidationError("request body is required")
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("request body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")
    message = data.get("message")
    if not isinstance(message, str):
        raise ValidationError("message is required and must be a string")
    return message


def _decode_body(event: Mapping[str, Any]) -> str | None:
    raw = event.get("body")
    if raw is None:
        return None
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValidationError("request body must be valid base64-encoded UTF-8") from exc
    return str(raw)


def _extract_cookie_value(event: Mapping[str, Any], headers: Mapping[str, str]) -> str | None:
    cookies = event.get("cookies")
    if cookies:
        return parse_session_cookie("; ".join(cookies))
    return parse_session_cookie(_header(headers, "cookie"))


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return str(value)
    return None
