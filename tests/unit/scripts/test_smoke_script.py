"""Unit tests for `tests/smoke/test_smoke.py`'s pure logic (percentile math,
response parsing, CORS/shape checks, session orchestration, report
formatting). A `FakeTransport` stands in for the real HTTP client so these
tests never touch the network — the smoke script itself is exercised for
real only by a human, against a deployed stack (`chat-endpoint` spec,
*End-to-End Latency Budget*; tasks.md 9.3).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

import pytest
from smoke.test_smoke import (
    COLD_SLO_SECONDS,
    DEFAULT_ALLOWED_HOST,
    DEFAULT_QUESTIONS_PER_SESSION,
    DEFAULT_SESSIONS,
    WARM_SLO_SECONDS,
    HttpResponse,
    TargetValidationError,
    _SameHostRedirectHandler,
    check_response_shape,
    check_set_cookie_security_flags,
    check_single_cors_header,
    compute_percentile,
    count_header,
    format_report,
    run_session,
    run_smoke_test,
    send_chat_request,
    validate_target_host,
)


class _FakeTransport:
    """Returns one scripted `HttpResponse` per call, in order."""

    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> HttpResponse:
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        return self._responses[len(self.calls) - 1]


def _ok_response(
    answer: str = "I have worked with Python.", language: str = "en", cors_headers: int = 1
) -> HttpResponse:
    headers = [("Content-Type", "application/json")]
    headers += [("Access-Control-Allow-Origin", "https://sergiomondragon.com")] * cors_headers
    headers.append(("Set-Cookie", "session=abc123; HttpOnly; Secure; SameSite=Lax; Path=/"))
    body = json.dumps({"answer": answer, "language": language}).encode()
    return HttpResponse(status=200, headers=headers, body=body)


def _rate_limited_response() -> HttpResponse:
    body = json.dumps({"error": "rate_limited"}).encode()
    return HttpResponse(status=429, headers=[("Retry-After", "60")], body=body)


def _ok_response_missing_cookie_flag(answer: str = "I have worked with Python.", language: str = "en") -> HttpResponse:
    """Same as `_ok_response` but the `Set-Cookie` header is missing `Secure`
    — used to exercise the flag check's failure path."""
    headers = [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "https://sergiomondragon.com"),
        ("Set-Cookie", "session=abc123; HttpOnly; SameSite=Lax; Path=/"),
    ]
    body = json.dumps({"answer": answer, "language": language}).encode()
    return HttpResponse(status=200, headers=headers, body=body)


# --- compute_percentile -------------------------------------------------------


def test_compute_percentile_p50_of_five_values() -> None:
    assert compute_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0


def test_compute_percentile_p95_of_twenty_values() -> None:
    values = [float(i) for i in range(1, 21)]
    assert compute_percentile(values, 95) == 19.0


def test_compute_percentile_single_value() -> None:
    assert compute_percentile([7.5], 95) == 7.5


# --- count_header / check_single_cors_header ----------------------------------


def test_count_header_is_case_insensitive() -> None:
    headers = [("access-control-allow-origin", "x"), ("Content-Type", "y")]
    assert count_header(headers, "Access-Control-Allow-Origin") == 1


def test_check_single_cors_header_true_for_exactly_one() -> None:
    assert check_single_cors_header(_ok_response(cors_headers=1).headers) is True


def test_check_single_cors_header_false_for_two() -> None:
    assert check_single_cors_header(_ok_response(cors_headers=2).headers) is False


def test_check_single_cors_header_false_for_zero() -> None:
    assert check_single_cors_header(_ok_response(cors_headers=0).headers) is False


# --- check_response_shape ------------------------------------------------------


def test_check_response_shape_accepts_valid_answer() -> None:
    result = send_chat_request(_FakeTransport([_ok_response()]), "https://api.example.com", "hi")
    assert check_response_shape(result) is True


def test_check_response_shape_rejects_wrong_status() -> None:
    result = send_chat_request(_FakeTransport([_rate_limited_response()]), "https://api.example.com", "hi")
    assert check_response_shape(result) is False


# --- send_chat_request ---------------------------------------------------------


def test_send_chat_request_parses_answer_language_and_cookie() -> None:
    transport = _FakeTransport([_ok_response(answer="Nice work", language="en")])

    result = send_chat_request(transport, "https://api.example.com", "What did you build?", origin="https://x.com")

    assert result.answer == "Nice work"
    assert result.language == "en"
    assert result.set_cookie == "session=abc123"
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"] == "https://api.example.com/v1/chat"
    assert transport.calls[0]["headers"]["Origin"] == "https://x.com"
    assert json.loads(transport.calls[0]["body"]) == {"message": "What did you build?"}


def test_send_chat_request_reuses_cookie_when_given() -> None:
    transport = _FakeTransport([_ok_response()])

    send_chat_request(transport, "https://api.example.com", "hi", cookie="session=abc123")

    assert transport.calls[0]["headers"]["Cookie"] == "session=abc123"


def test_send_chat_request_measures_elapsed_via_injected_clock() -> None:
    clock_values = iter([10.0, 10.25])
    transport = _FakeTransport([_ok_response()])

    result = send_chat_request(transport, "https://api.example.com", "hi", clock_fn=lambda: next(clock_values))

    assert result.elapsed_seconds == 0.25


# --- run_session ----------------------------------------------------------------


def test_run_session_first_request_has_no_cookie_and_rest_reuse_it() -> None:
    transport = _FakeTransport([_ok_response(), _ok_response(), _ok_response()])

    session = run_session(transport, "https://api.example.com", ["q1", "q2", "q3"])

    assert "Cookie" not in transport.calls[0]["headers"]
    assert transport.calls[1]["headers"]["Cookie"] == "session=abc123"
    assert transport.calls[2]["headers"]["Cookie"] == "session=abc123"
    assert len(session.warm) == 2


# --- run_smoke_test / format_report ---------------------------------------------


def test_run_smoke_test_passes_with_healthy_responses() -> None:
    responses = [_ok_response() for _ in range(6)]  # 2 sessions x 3 questions
    transport = _FakeTransport(responses)
    clock_values = iter(x * 0.5 for x in range(100))

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=2,
        questions_per_session=3,
        clock_fn=lambda: next(clock_values),
    )

    assert report.cors_pass is True
    assert report.shape_pass is True
    assert report.cold_pass is True
    assert report.warm_pass is True
    assert report.passed is True
    assert len(report.cold_latencies) == 2
    assert len(report.warm_latencies) == 4


def test_run_smoke_test_fails_when_cold_p95_exceeds_slo() -> None:
    transport = _FakeTransport([_ok_response(), _ok_response()])
    # First call: 0 -> 11s elapsed (exceeds the 10s cold SLO). Second call: fast warm request.
    clock_values = iter([0.0, 11.0, 11.0, 11.2])

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=1,
        questions_per_session=2,
        clock_fn=lambda: next(clock_values),
    )

    assert report.cold_pass is False
    assert report.passed is False


def test_run_smoke_test_exhaust_limits_checks_429_and_retry_after() -> None:
    responses = [_ok_response(), _rate_limited_response()]
    transport = _FakeTransport(responses)
    clock_values = iter(x * 0.1 for x in range(20))

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=1,
        questions_per_session=1,
        exhaust_limits=True,
        session_daily_limit=1,
        clock_fn=lambda: next(clock_values),
    )

    assert report.rate_limit_pass is True


def test_run_smoke_test_exhaust_limits_fails_without_retry_after() -> None:
    ok = _ok_response()
    bad_429 = HttpResponse(status=429, headers=[], body=b"{}")
    transport = _FakeTransport([ok, bad_429])
    clock_values = iter(x * 0.1 for x in range(20))

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=1,
        questions_per_session=1,
        exhaust_limits=True,
        session_daily_limit=1,
        clock_fn=lambda: next(clock_values),
    )

    assert report.rate_limit_pass is False
    assert report.passed is False


def test_run_smoke_test_expect_substring_checked_against_answers() -> None:
    transport = _FakeTransport([_ok_response(answer="Built with React and TypeScript")])
    clock_values = iter(x * 0.1 for x in range(20))

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=1,
        questions_per_session=1,
        expect_substring="React",
        clock_fn=lambda: next(clock_values),
    )

    assert report.expected_fact_found is True


def test_format_report_includes_pass_fail_and_latency_numbers() -> None:
    transport = _FakeTransport([_ok_response(), _ok_response()])
    clock_values = iter([0.0, 1.0, 1.0, 1.2])

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=1,
        questions_per_session=2,
        clock_fn=lambda: next(clock_values),
    )

    text = format_report(report)

    assert "PASS" in text
    assert "warm p50=" in text
    assert "cold p50=" in text
    assert str(WARM_SLO_SECONDS) in text
    assert str(COLD_SLO_SECONDS) in text


# --- validate_target_host -------------------------------------------------------


def test_validate_target_host_accepts_https_expected_host() -> None:
    host = validate_target_host(f"https://{DEFAULT_ALLOWED_HOST}", DEFAULT_ALLOWED_HOST)
    assert host == DEFAULT_ALLOWED_HOST


def test_validate_target_host_rejects_non_https_scheme() -> None:
    with pytest.raises(TargetValidationError, match="https"):
        validate_target_host(f"http://{DEFAULT_ALLOWED_HOST}", DEFAULT_ALLOWED_HOST)


def test_validate_target_host_rejects_unexpected_host() -> None:
    with pytest.raises(TargetValidationError, match="evil.example.com"):
        validate_target_host("https://evil.example.com", DEFAULT_ALLOWED_HOST)


def test_validate_target_host_allows_override_via_allowed_host_argument() -> None:
    host = validate_target_host("https://staging.example.com", "staging.example.com")
    assert host == "staging.example.com"


# --- _SameHostRedirectHandler ----------------------------------------------------


def test_same_host_redirect_handler_blocks_cross_host_redirect() -> None:
    handler = _SameHostRedirectHandler()
    req = urllib.request.Request(f"https://{DEFAULT_ALLOWED_HOST}/v1/chat")

    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/steal")


def test_same_host_redirect_handler_allows_same_host_redirect() -> None:
    handler = _SameHostRedirectHandler()
    req = urllib.request.Request(f"https://{DEFAULT_ALLOWED_HOST}/v1/chat")

    new_request = handler.redirect_request(
        req, None, 302, "Found", {}, f"https://{DEFAULT_ALLOWED_HOST}/v1/chat-redirected"
    )

    assert new_request is not None
    assert new_request.full_url == f"https://{DEFAULT_ALLOWED_HOST}/v1/chat-redirected"


# --- percentile honesty at small N -----------------------------------------------


def test_defaults_give_at_least_20_samples_per_population() -> None:
    """cold N == sessions, warm N == sessions * (questions_per_session - 1) —
    both must reach the 20-sample floor a nearest-rank p95 needs to mean
    anything more than max()."""
    assert DEFAULT_SESSIONS == 20
    assert DEFAULT_SESSIONS >= 20
    assert DEFAULT_SESSIONS * (DEFAULT_QUESTIONS_PER_SESSION - 1) >= 20


def test_format_report_labels_p95_normally_with_enough_samples() -> None:
    responses = [_ok_response() for _ in range(60)]  # 20 sessions x 3 questions
    transport = _FakeTransport(responses)
    clock_values = iter(x * 0.1 for x in range(200))

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=20,
        questions_per_session=3,
        clock_fn=lambda: next(clock_values),
    )

    text = format_report(report)

    assert " p95=" in text
    assert text.count(" p95=") == 2
    assert "too few samples" not in text


def test_format_report_labels_max_as_dishonest_p95_below_20_samples() -> None:
    responses = [_ok_response() for _ in range(6)]  # 2 sessions x 3 questions
    transport = _FakeTransport(responses)
    clock_values = iter(x * 0.1 for x in range(100))

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=2,
        questions_per_session=3,
        clock_fn=lambda: next(clock_values),
    )

    text = format_report(report)

    assert "max (N=2, too few samples for a p95)=" in text
    assert "max (N=4, too few samples for a p95)=" in text


# --- Set-Cookie security flags ----------------------------------------------------


def test_check_set_cookie_security_flags_true_when_all_flags_present() -> None:
    assert check_set_cookie_security_flags("session=abc123; HttpOnly; Secure; SameSite=Lax; Path=/") is True


def test_check_set_cookie_security_flags_false_when_a_flag_is_missing() -> None:
    assert check_set_cookie_security_flags("session=abc123; HttpOnly; SameSite=Lax; Path=/") is False


def test_send_chat_request_flags_cookie_missing_a_security_attribute() -> None:
    transport = _FakeTransport([_ok_response_missing_cookie_flag()])

    result = send_chat_request(transport, "https://api.example.com", "hi")

    assert result.cookie_flags_valid is False
    assert result.set_cookie == "session=abc123"


def test_send_chat_request_cookie_flags_valid_when_all_present() -> None:
    transport = _FakeTransport([_ok_response()])

    result = send_chat_request(transport, "https://api.example.com", "hi")

    assert result.cookie_flags_valid is True


def test_run_smoke_test_fails_when_set_cookie_is_missing_a_security_flag() -> None:
    transport = _FakeTransport([_ok_response_missing_cookie_flag(), _ok_response()])
    clock_values = iter(x * 0.1 for x in range(20))

    report = run_smoke_test(
        transport,
        base_url="https://api.example.com",
        sessions=1,
        questions_per_session=2,
        clock_fn=lambda: next(clock_values),
    )

    assert report.cookie_flags_pass is False
    assert report.passed is False
