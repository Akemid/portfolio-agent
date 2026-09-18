"""Unit tests for `tests/smoke/test_smoke.py`'s pure logic (percentile math,
response parsing, CORS/shape checks, session orchestration, report
formatting). A `FakeTransport` stands in for the real HTTP client so these
tests never touch the network — the smoke script itself is exercised for
real only by a human, against a deployed stack (`chat-endpoint` spec,
*End-to-End Latency Budget*; tasks.md 9.3).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from smoke.test_smoke import (
    COLD_SLO_SECONDS,
    WARM_SLO_SECONDS,
    HttpResponse,
    check_response_shape,
    check_single_cors_header,
    compute_percentile,
    count_header,
    format_report,
    run_session,
    run_smoke_test,
    send_chat_request,
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
