"""Post-deploy smoke test against a real, deployed `POST /v1/chat` (tasks.md
9.3; `chat-endpoint` spec, *End-to-End Latency Budget*; design.md SS7 RQ-5,
SS13 carried risk). Named `test_smoke.py` (matching `tests/{unit,contract,
smoke}` per Phase 1's package skeleton) but defines no `def test_*` function
of its own — pytest collects this module (harmless: no assertions run, no
network touched) while never counting it toward the `--cov=src` gate or the
default `uv run pytest` run, per this task's own "never runs in CI" note. Its
pure logic (percentile math, response parsing, session orchestration, report
formatting) is unit-tested with an injected fake transport in
`tests/unit/scripts/test_smoke_script.py`.

Usage (never run against AWS from an automated agent — a human runs this
manually after `cdk deploy`, per `docs/runbooks/deploy.md`):

    uv run python tests/smoke/test_smoke.py --api-url https://api.sergiomondragon.com
    uv run python tests/smoke/test_smoke.py  # resolves ApiUrl from the portfolio-agent-api stack
    uv run python tests/smoke/test_smoke.py --exhaust-limits  # also burns one session's daily cap

Measures the split latency SLO (design.md, owner-confirmed): a p95 under
3.5 s for warm (already-answered-once) sessions, and a p95 under 10 s for the
first request of a fresh session (AgentCore Runtime cold start). Percentiles
use the nearest-rank method (no numpy dependency, matches this project's
`agent`-group dependency floor for the same reason `knowledge_base.py`
avoided `strands-agents-tools`).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any, Protocol

CHAT_PATH = "/v1/chat"
DEFAULT_STACK_NAME = "portfolio-agent-api"
DEFAULT_REGION = "us-east-1"
DEFAULT_ORIGIN = "https://sergiomondragon.com"
DEFAULT_SESSIONS = 3
DEFAULT_QUESTIONS_PER_SESSION = 3
DEFAULT_SESSION_DAILY_LIMIT = 10
# owner-confirmed split latency SLO (design.md SS1, "Success Criteria"; state.yaml owner_confirmed).
WARM_SLO_SECONDS = 3.5
COLD_SLO_SECONDS = 10.0
_DEFAULT_QUESTIONS = (
    "What is your professional experience?",
    "What technologies have you worked with?",
    "Tell me about a project you built.",
)
_CORS_HEADER = "access-control-allow-origin"
_RETRY_AFTER_HEADER = "retry-after"
_SET_COOKIE_HEADER = "set-cookie"


@dataclass(frozen=True)
class HttpResponse:
    """A transport-agnostic HTTP response. `headers` keeps every occurrence
    (a list of pairs, not a dict) so duplicate headers — like a doubled
    `Access-Control-Allow-Origin` — are detectable."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes


class Transport(Protocol):
    """The one method the smoke test needs from an HTTP client — small
    enough that tests inject a fake and never touch the network."""

    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> HttpResponse: ...


class UrllibTransport:
    """Real transport, used only by `main()` — stdlib only, no new dependency."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds

    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> HttpResponse:
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:  # noqa: S310
                return HttpResponse(status=resp.status, headers=list(resp.getheaders()), body=resp.read())
        except urllib.error.HTTPError as exc:
            error_headers = list(exc.headers.items()) if exc.headers is not None else []
            return HttpResponse(status=exc.code, headers=error_headers, body=exc.read())


@dataclass(frozen=True)
class ChatResult:
    """One `POST /v1/chat` call's outcome and timing."""

    status: int
    elapsed_seconds: float
    answer: str | None
    language: str | None
    set_cookie: str | None
    headers: list[tuple[str, str]]


@dataclass(frozen=True)
class SessionRun:
    """One simulated visitor session: exactly one cold (first) request, then
    zero or more warm (cookie-reusing) requests."""

    cold: ChatResult
    warm: list[ChatResult]


@dataclass(frozen=True)
class SmokeReport:
    """The full smoke-test outcome: raw measurements plus each pass/fail check."""

    cold_latencies: list[float]
    warm_latencies: list[float]
    cold_p50: float
    cold_p95: float
    warm_p50: float
    warm_p95: float
    cold_pass: bool
    warm_pass: bool
    cors_pass: bool
    shape_pass: bool
    rate_limit_pass: bool | None
    expected_fact_found: bool | None
    passed: bool
    warm_slo_seconds: float = WARM_SLO_SECONDS
    cold_slo_seconds: float = COLD_SLO_SECONDS


def compute_percentile(values: Sequence[float], percentile: float) -> float:
    """Nearest-rank percentile — no numpy dependency (see module docstring)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def count_header(headers: Sequence[tuple[str, str]], name: str) -> int:
    lowered = name.lower()
    return sum(1 for key, _ in headers if key.lower() == lowered)


def check_single_cors_header(headers: Sequence[tuple[str, str]]) -> bool:
    """`chat-endpoint` spec, *CORS Restriction*; PR8b's carried
    `to_verify_at_deploy` risk — API Gateway's own CORS config must be the
    ONE source of the header, not doubled by the Lambda's own `responder.py`."""
    return count_header(headers, _CORS_HEADER) == 1


def check_response_shape(result: ChatResult) -> bool:
    """`chat-endpoint` spec, *Response Contract*: `{answer: string, language: "en"|"es"}`."""
    return result.status == 200 and isinstance(result.answer, str) and result.language in ("en", "es")


def _first_header_value(headers: Sequence[tuple[str, str]], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return None


def _parse_cookie_pair(set_cookie: str) -> str:
    """Reduce a full `Set-Cookie` header to just the `name=value` pair the
    client resends as `Cookie` — the same reduction any real browser does."""
    return set_cookie.split(";", 1)[0].strip()


def send_chat_request(
    transport: Transport,
    base_url: str,
    message: str,
    *,
    cookie: str | None = None,
    origin: str | None = DEFAULT_ORIGIN,
    clock_fn: Callable[[], float] = time.monotonic,
) -> ChatResult:
    """Send one `POST /v1/chat` and parse the response, timing the call with
    `clock_fn` (injected so tests control elapsed time without sleeping)."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    if cookie is not None:
        headers["Cookie"] = cookie

    body = json.dumps({"message": message}).encode()
    start = clock_fn()
    response = transport.request("POST", f"{base_url}{CHAT_PATH}", headers, body)
    elapsed = clock_fn() - start

    answer: str | None = None
    language: str | None = None
    if response.status == 200:
        try:
            data: Any = json.loads(response.body)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            answer = data.get("answer") if isinstance(data.get("answer"), str) else None
            language = data.get("language") if isinstance(data.get("language"), str) else None

    raw_cookie = _first_header_value(response.headers, _SET_COOKIE_HEADER)
    set_cookie = _parse_cookie_pair(raw_cookie) if raw_cookie is not None else None

    return ChatResult(
        status=response.status,
        elapsed_seconds=elapsed,
        answer=answer,
        language=language,
        set_cookie=set_cookie,
        headers=response.headers,
    )


def run_session(
    transport: Transport,
    base_url: str,
    questions: Sequence[str],
    *,
    origin: str | None = DEFAULT_ORIGIN,
    clock_fn: Callable[[], float] = time.monotonic,
) -> SessionRun:
    """Run one fresh session: the first question has no cookie (cold); every
    following question reuses the cookie the first response set (warm)."""
    if not questions:
        raise ValueError("questions must be non-empty")

    cold = send_chat_request(transport, base_url, questions[0], cookie=None, origin=origin, clock_fn=clock_fn)
    warm: list[ChatResult] = []
    for question in questions[1:]:
        warm.append(
            send_chat_request(transport, base_url, question, cookie=cold.set_cookie, origin=origin, clock_fn=clock_fn)
        )
    return SessionRun(cold=cold, warm=warm)


def _exhaust_session_limit(
    transport: Transport,
    base_url: str,
    cookie: str | None,
    already_sent: int,
    session_daily_limit: int,
    *,
    origin: str | None,
    clock_fn: Callable[[], float],
) -> ChatResult:
    """Send enough extra requests on the same session to exceed
    `session_daily_limit`, returning the last (expected 429) response."""
    remaining = max(1, session_daily_limit - already_sent + 1)
    result: ChatResult | None = None
    for index in range(remaining):
        result = send_chat_request(
            transport, base_url, f"exhaust-limits probe {index}", cookie=cookie, origin=origin, clock_fn=clock_fn
        )
    assert result is not None
    return result


def run_smoke_test(
    transport: Transport,
    *,
    base_url: str,
    sessions: int = DEFAULT_SESSIONS,
    questions_per_session: int = DEFAULT_QUESTIONS_PER_SESSION,
    questions: Sequence[str] = _DEFAULT_QUESTIONS,
    origin: str | None = DEFAULT_ORIGIN,
    exhaust_limits: bool = False,
    session_daily_limit: int = DEFAULT_SESSION_DAILY_LIMIT,
    warm_slo_seconds: float = WARM_SLO_SECONDS,
    cold_slo_seconds: float = COLD_SLO_SECONDS,
    expect_substring: str | None = None,
    clock_fn: Callable[[], float] = time.monotonic,
) -> SmokeReport:
    """Run `sessions` fresh sessions of `questions_per_session` questions
    each, check CORS/shape on every response, and — only when
    `exhaust_limits=True` (opt-in: a normal run must not burn the daily
    quota) — exceed the last session's cap and assert a 429 with
    `Retry-After`."""
    cold_latencies: list[float] = []
    warm_latencies: list[float] = []
    cors_ok = True
    shape_ok = True
    expected_fact_found: bool | None = None if expect_substring is None else False
    rate_limit_pass: bool | None = None
    all_answers: list[str] = []
    last_session: SessionRun | None = None

    for _session_index in range(sessions):
        session_questions = [questions[i % len(questions)] for i in range(questions_per_session)]
        session = run_session(transport, base_url, session_questions, origin=origin, clock_fn=clock_fn)
        last_session = session

        cold_latencies.append(session.cold.elapsed_seconds)
        warm_latencies.extend(result.elapsed_seconds for result in session.warm)

        for result in (session.cold, *session.warm):
            cors_ok = cors_ok and check_single_cors_header(result.headers)
            shape_ok = shape_ok and check_response_shape(result)
            if result.answer is not None:
                all_answers.append(result.answer)

    if expect_substring is not None:
        expected_fact_found = any(expect_substring in answer for answer in all_answers)

    if exhaust_limits and last_session is not None:
        final = _exhaust_session_limit(
            transport,
            base_url,
            last_session.cold.set_cookie,
            already_sent=questions_per_session,
            session_daily_limit=session_daily_limit,
            origin=origin,
            clock_fn=clock_fn,
        )
        rate_limit_pass = final.status == 429 and _first_header_value(final.headers, _RETRY_AFTER_HEADER) is not None

    cold_p50 = compute_percentile(cold_latencies, 50)
    cold_p95 = compute_percentile(cold_latencies, 95)
    warm_p50 = compute_percentile(warm_latencies, 50)
    warm_p95 = compute_percentile(warm_latencies, 95)
    cold_pass = cold_p95 < cold_slo_seconds
    warm_pass = warm_p95 < warm_slo_seconds if warm_latencies else True

    passed = (
        cors_ok
        and shape_ok
        and cold_pass
        and warm_pass
        and rate_limit_pass is not False
        and expected_fact_found is not False
    )

    return SmokeReport(
        cold_latencies=cold_latencies,
        warm_latencies=warm_latencies,
        cold_p50=cold_p50,
        cold_p95=cold_p95,
        warm_p50=warm_p50,
        warm_p95=warm_p95,
        cold_pass=cold_pass,
        warm_pass=warm_pass,
        cors_pass=cors_ok,
        shape_pass=shape_ok,
        rate_limit_pass=rate_limit_pass,
        expected_fact_found=expected_fact_found,
        passed=passed,
        warm_slo_seconds=warm_slo_seconds,
        cold_slo_seconds=cold_slo_seconds,
    )


def _status_word(value: bool) -> str:
    return "PASS" if value else "FAIL"


def format_report(report: SmokeReport) -> str:
    lines = [
        f"cold p50={report.cold_p50:.2f}s p95={report.cold_p95:.2f}s "
        f"(SLO < {report.cold_slo_seconds}s): {_status_word(report.cold_pass)}",
        f"warm p50={report.warm_p50:.2f}s p95={report.warm_p95:.2f}s "
        f"(SLO < {report.warm_slo_seconds}s): {_status_word(report.warm_pass)}",
        f"CORS (exactly one Access-Control-Allow-Origin): {_status_word(report.cors_pass)}",
        f"Response shape ({{answer, language}}): {_status_word(report.shape_pass)}",
    ]
    if report.rate_limit_pass is not None:
        lines.append(f"429 + Retry-After on limit exhaustion: {_status_word(report.rate_limit_pass)}")
    if report.expected_fact_found is not None:
        lines.append(f"Expected fact found in an answer: {_status_word(report.expected_fact_found)}")
    lines.append(f"Overall: {_status_word(report.passed)}")
    return "\n".join(lines)


def resolve_api_url(cfn_client: Any, stack_name: str) -> str:
    response = cfn_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", [])
    if not stacks:
        raise LookupError(f"stack not found: {stack_name}")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stacks[0].get("Outputs", [])}
    if "ApiUrl" not in outputs:
        raise LookupError(f"stack {stack_name} has no ApiUrl output")
    return str(outputs["ApiUrl"]).rstrip("/")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=None, help="override the resolved ApiUrl stack output")
    parser.add_argument("--stack-name", default=DEFAULT_STACK_NAME)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)
    parser.add_argument("--questions-per-session", type=int, default=DEFAULT_QUESTIONS_PER_SESSION)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--session-daily-limit", type=int, default=DEFAULT_SESSION_DAILY_LIMIT)
    parser.add_argument(
        "--exhaust-limits",
        action="store_true",
        help="also burn one session's full daily cap to check the 429 path (off by default)",
    )
    parser.add_argument("--expect-substring", default=None, help="assert this substring appears in an answer")
    parser.add_argument("--warm-slo", type=float, default=WARM_SLO_SECONDS)
    parser.add_argument("--cold-slo", type=float, default=COLD_SLO_SECONDS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, stdout: IO[str] = sys.stdout) -> int:
    args = _parse_args(argv)

    base_url = args.api_url
    if base_url is None:
        import boto3  # local import: keeps this module importable with zero AWS SDK setup

        cfn_client = boto3.client("cloudformation", region_name=args.region)
        base_url = resolve_api_url(cfn_client, args.stack_name)

    report = run_smoke_test(
        UrllibTransport(),
        base_url=base_url,
        sessions=args.sessions,
        questions_per_session=args.questions_per_session,
        origin=args.origin,
        exhaust_limits=args.exhaust_limits,
        session_daily_limit=args.session_daily_limit,
        warm_slo_seconds=args.warm_slo,
        cold_slo_seconds=args.cold_slo,
        expect_substring=args.expect_substring,
    )
    print(format_report(report), file=stdout)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
