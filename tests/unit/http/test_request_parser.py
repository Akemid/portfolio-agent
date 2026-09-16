"""Unit tests for `api.http.request_parser` (`chat-endpoint` spec, *Request
Contract*; `rate-limiting` spec, *Header Spoofing Attempt*).

Fixtures shape a realistic API Gateway HTTP API v2 Lambda-proxy event
(`event.requestContext.http`, `event.cookies`, `event.headers`).
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from api.domain.errors import ValidationError
from api.http.request_parser import extract_message, parse_event

SESSION_ID = "a" * 43  # shape of `secrets.token_urlsafe(32)` output


def _v2_event(
    *,
    method: str = "POST",
    path: str = "/v1/chat",
    body: str | None = '{"message": "hello"}',
    is_base64_encoded: bool = False,
    cookies: list[str] | None = None,
    headers: dict[str, str] | None = None,
    source_ip: str = "203.0.113.7",
) -> dict[str, Any]:
    """Build a minimal, realistic API Gateway HTTP API v2 event."""
    event: dict[str, Any] = {
        "requestContext": {"http": {"method": method, "path": path, "sourceIp": source_ip}},
        "headers": headers or {},
        "isBase64Encoded": is_base64_encoded,
    }
    if body is not None:
        event["body"] = body
    if cookies is not None:
        event["cookies"] = cookies
    return event


def test_parses_valid_body_and_cookie() -> None:
    event = _v2_event(cookies=[f"session_id={SESSION_ID}", "theme=dark"])

    request = parse_event(event)

    assert request.method == "POST"
    assert request.path == "/v1/chat"
    assert request.body == '{"message": "hello"}'
    assert request.cookie_value == SESSION_ID
    assert request.source_ip == "203.0.113.7"


def test_uses_source_ip_and_ignores_x_forwarded_for() -> None:
    event = _v2_event(source_ip="203.0.113.7", headers={"X-Forwarded-For": "198.51.100.1"})

    request = parse_event(event)

    assert request.source_ip == "203.0.113.7"


def test_missing_source_ip_is_none() -> None:
    event = _v2_event()
    del event["requestContext"]["http"]["sourceIp"]

    request = parse_event(event)

    assert request.source_ip is None


def test_blank_source_ip_is_none() -> None:
    event = _v2_event(source_ip="")

    request = parse_event(event)

    assert request.source_ip is None


def test_origin_header_is_case_insensitive() -> None:
    event = _v2_event(headers={"Origin": "https://sergiomondragon.com"})

    request = parse_event(event)

    assert request.origin == "https://sergiomondragon.com"


def test_missing_origin_header_is_none() -> None:
    request = parse_event(_v2_event(headers={}))

    assert request.origin is None


def test_cookie_header_fallback_is_case_insensitive_when_cookies_list_is_absent() -> None:
    event = _v2_event(headers={"cookie": f"session_id={SESSION_ID}"})

    request = parse_event(event)

    assert request.cookie_value == SESSION_ID


def test_missing_cookie_is_none() -> None:
    request = parse_event(_v2_event(cookies=[]))

    assert request.cookie_value is None


def test_base64_encoded_body_is_decoded() -> None:
    raw = '{"message": "hola"}'
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    event = _v2_event(body=encoded, is_base64_encoded=True)

    request = parse_event(event)

    assert request.body == raw


def test_missing_body_is_none() -> None:
    request = parse_event(_v2_event(body=None))

    assert request.body is None


def test_malformed_base64_body_raises_validation_error() -> None:
    event = _v2_event(body="%%%not-base64%%%", is_base64_encoded=True)

    with pytest.raises(ValidationError):
        parse_event(event)


def test_base64_body_with_invalid_utf8_raises_validation_error() -> None:
    encoded = base64.b64encode(b"\xff\xfe").decode("ascii")
    event = _v2_event(body=encoded, is_base64_encoded=True)

    with pytest.raises(ValidationError):
        parse_event(event)


def test_extract_message_returns_the_message_field() -> None:
    body = '{"message": "What is your experience with React?"}'

    assert extract_message(body) == "What is your experience with React?"


def test_extract_message_rejects_invalid_json() -> None:
    with pytest.raises(ValidationError):
        extract_message("{not valid json")


def test_extract_message_rejects_missing_body() -> None:
    with pytest.raises(ValidationError):
        extract_message(None)


def test_extract_message_rejects_non_object_body() -> None:
    with pytest.raises(ValidationError):
        extract_message(json.dumps(["not", "an", "object"]))


def test_extract_message_rejects_missing_message_key() -> None:
    with pytest.raises(ValidationError):
        extract_message(json.dumps({"not_message": "hi"}))


def test_extract_message_rejects_non_string_message() -> None:
    with pytest.raises(ValidationError):
        extract_message(json.dumps({"message": 12345}))
