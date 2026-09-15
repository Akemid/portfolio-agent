"""API Gateway HTTP API v2 event -> `IncomingRequest` (design.md §4 `handler.py`
composition root; `chat-endpoint` spec, *Request Contract*; `rate-limiting`
spec, *Header Spoofing Attempt*).

`parse_event` never raises: it only unwraps the AWS event shape, so a caller
always has `origin`/`cookie_value` available to build an error response even
when the body turns out to be invalid. `extract_message` is the separate,
raising step that turns the raw body into the validated `message` string
(`chat-endpoint` spec, *Invalid JSON*) — kept apart so a parsing failure still
leaves `IncomingRequest` usable for CORS/error headers.
"""

from __future__ import annotations

import base64
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
    source_ip: str


def parse_event(event: Mapping[str, Any]) -> IncomingRequest:
    """Extract method, path, body, cookie value, origin, and source IP.

    `source_ip` comes ONLY from `requestContext.http.sourceIp` — never from
    an `X-Forwarded-For` header, which a client fully controls and could
    forge to evade the per-IP rate limit (`rate-limiting` spec).
    """
    http = event.get("requestContext", {}).get("http", {})
    headers = event.get("headers") or {}
    return IncomingRequest(
        method=http.get("method", ""),
        path=http.get("path", ""),
        body=_decode_body(event),
        cookie_value=_extract_cookie_value(event, headers),
        origin=_header(headers, "origin"),
        source_ip=http.get("sourceIp", ""),
    )


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
        return base64.b64decode(raw).decode("utf-8")
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
