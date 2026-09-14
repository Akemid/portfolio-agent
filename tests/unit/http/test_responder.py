"""Unit tests for `api.http.responder` (`chat-endpoint` spec; `rate-limiting` spec)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from api.domain.errors import RateLimited, UpstreamError, UpstreamTimeout, ValidationError
from api.domain.models import AgentAnswer, Session
from api.http.responder import build_response, cors_headers, error_response, success_response
from api.usecases.answer_question import AnswerResult

ISSUED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
ALLOWED_ORIGIN = "https://sergiomondragon.com"
ALLOWLIST = (ALLOWED_ORIGIN, "http://localhost:4321")


def _result(*, session_is_new: bool) -> AnswerResult:
    session = Session(session_id="s" * 30, issued_at=ISSUED_AT)
    return AnswerResult(
        answer=AgentAnswer(answer="Hi there.", language="en"),
        session=session,
        session_is_new=session_is_new,
    )


def test_build_response_sets_json_content_type_and_serializes_body() -> None:
    response = build_response(200, {"answer": "hi"})

    assert response["headers"]["Content-Type"] == "application/json"
    assert json.loads(response["body"]) == {"answer": "hi"}


def test_success_response_shape() -> None:
    response = success_response(_result(session_is_new=False), origin=ALLOWED_ORIGIN, allowlist=ALLOWLIST)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"answer": "Hi there.", "language": "en"}
    assert "Set-Cookie" not in response["headers"]
    assert response["headers"]["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN


def test_new_session_sets_cookie() -> None:
    result = _result(session_is_new=True)

    response = success_response(result, origin=ALLOWED_ORIGIN, allowlist=ALLOWLIST)

    assert response["headers"]["Set-Cookie"].startswith(f"session_id={result.session.session_id}")


def test_429_includes_retry_after_and_error_body() -> None:
    error = RateLimited(scope="ip", retry_after_seconds=42)

    response = error_response(error, origin=ALLOWED_ORIGIN, allowlist=ALLOWLIST)

    assert response["statusCode"] == 429
    assert response["headers"]["Retry-After"] == "42"
    assert json.loads(response["body"]) == {"error": "rate_limited", "scope": "ip"}


def test_400_for_validation_error() -> None:
    response = error_response(ValidationError("message must not be empty"), origin=ALLOWED_ORIGIN, allowlist=ALLOWLIST)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"error": "invalid_request"}


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_body"),
    [
        (UpstreamError("boto3 ClientError: arn:aws:iam::123456789012:role/secret"), 502, {"error": "upstream_error"}),
        (
            UpstreamTimeout("timed out after 3.5s calling arn:aws:bedrock-agentcore:..."),
            504,
            {"error": "upstream_timeout"},
        ),
    ],
)
def test_502_and_504_do_not_leak_exception_detail(
    error: Exception, expected_status: int, expected_body: dict[str, str]
) -> None:
    response = error_response(error, origin=ALLOWED_ORIGIN, allowlist=ALLOWLIST)

    assert response["statusCode"] == expected_status
    assert json.loads(response["body"]) == expected_body
    assert "arn:aws" not in response["body"]


def test_unexpected_error_maps_to_generic_500() -> None:
    response = error_response(RuntimeError("boom"), origin=ALLOWED_ORIGIN, allowlist=ALLOWLIST)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"error": "internal_error"}


def test_allowed_origin_included_in_cors_header() -> None:
    headers = cors_headers(ALLOWED_ORIGIN, ALLOWLIST)

    assert headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert headers["Access-Control-Allow-Credentials"] == "true"
    assert headers["Vary"] == "Origin"


def test_disallowed_origin_omitted_from_cors_header() -> None:
    headers = cors_headers("https://evil.example.com", ALLOWLIST)

    assert "Access-Control-Allow-Origin" not in headers
    assert "Access-Control-Allow-Credentials" not in headers
    assert headers["Vary"] == "Origin"


def test_missing_origin_omitted_from_cors_header() -> None:
    headers = cors_headers(None, ALLOWLIST)

    assert "Access-Control-Allow-Origin" not in headers
