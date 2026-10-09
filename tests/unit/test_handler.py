"""Unit tests for `api.handler` (design.md §4 composition root; `chat-endpoint`
spec). Wires `build_container` with in-memory fakes so no AWS call happens,
except the one "light contract" test that exercises real DynamoDB adapters
against the moto `dynamodb_table` fixture.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes.fake_agent_client import FakeAgentClient
from fakes.fake_rate_limiter import FakeRateLimiter
from fakes.fake_session_store import FakeSessionStore
from fakes.frozen_clock import FrozenClock

import api.handler as handler_module
from api.adapters.dynamo_rate_limiter import DynamoRateLimiter
from api.adapters.dynamo_session_store import DynamoSessionStore
from api.adapters.secure_ids import SecureIds
from api.config import Settings
from api.domain.models import AgentAnswer, RateLimitDecision
from api.handler import Container, build_container, lambda_handler

_NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
_ALLOWED_ORIGIN = "https://sergiomondragon.com"
_SOURCE_IP = "203.0.113.7"
_MESSAGE = "What is your experience with React?"

_REQUIRED_ENV = {
    "TABLE_NAME": "portfolio-agent-sessions",
    "AGENT_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test-agent",
}


def _event(
    *,
    body: str | None = None,
    cookies: list[str] | None = None,
    origin: str | None = _ALLOWED_ORIGIN,
    is_base64_encoded: bool = False,
) -> dict[str, Any]:
    return {
        "requestContext": {"http": {"method": "POST", "path": "/v1/chat", "sourceIp": _SOURCE_IP}},
        "headers": {"origin": origin} if origin else {},
        "cookies": cookies or [],
        "body": body if body is not None else json.dumps({"message": _MESSAGE}),
        "isBase64Encoded": is_base64_encoded,
    }


def _install_container(
    monkeypatch: pytest.MonkeyPatch, *, agent_client: Any = None, rate_limiter: Any = None
) -> FakeAgentClient:
    fake_agent = agent_client or FakeAgentClient()
    container = Container(
        session_store=FakeSessionStore(FrozenClock(_NOW)),
        rate_limiter=rate_limiter or FakeRateLimiter(),
        agent_client=fake_agent,
        clock=FrozenClock(_NOW),
        allowed_origins=(_ALLOWED_ORIGIN,),
    )
    monkeypatch.setattr(handler_module, "_container", container)
    return fake_agent


def test_handler_wires_real_adapter_types() -> None:
    container = build_container(Settings.from_env(_REQUIRED_ENV))

    assert isinstance(container.session_store, DynamoSessionStore)
    assert isinstance(container.rate_limiter, DynamoRateLimiter)
    assert container.allowed_origins == ("https://sergiomondragon.com",)


def test_build_container_reuses_an_injected_table_instead_of_creating_one() -> None:
    sentinel_table = object()

    container = build_container(Settings.from_env(_REQUIRED_ENV), table=sentinel_table)

    assert container.session_store._table is sentinel_table  # type: ignore[attr-defined]
    assert container.rate_limiter._table is sentinel_table  # type: ignore[attr-defined]


def test_get_container_builds_and_caches_from_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler_module, "_container", None)
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)

    first = handler_module._get_container()
    second = handler_module._get_container()

    assert first is second


def test_boto3_client_construction_includes_bedrock_agentcore() -> None:
    """Guards against the "bundled boto3 lacks the client" risk (design.md
    §12/13): if boto3/botocore ever ship without the `bedrock-agentcore`
    service model, this fails loudly at test time instead of at cold start.
    """
    container = build_container(Settings.from_env(_REQUIRED_ENV))

    assert container.agent_client is not None


def test_happy_path_new_session_sets_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch)

    response = lambda_handler(_event(), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"answer": "stub answer", "language": "en"}
    assert "Set-Cookie" in response["headers"]
    assert response["headers"]["Access-Control-Allow-Origin"] == _ALLOWED_ORIGIN


def test_happy_path_existing_session_does_not_set_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    session_store = FakeSessionStore(FrozenClock(_NOW))
    session = session_store.create()
    container = Container(
        session_store=session_store,
        rate_limiter=FakeRateLimiter(),
        agent_client=FakeAgentClient(),
        clock=FrozenClock(_NOW),
        allowed_origins=(_ALLOWED_ORIGIN,),
    )
    monkeypatch.setattr(handler_module, "_container", container)

    response = lambda_handler(_event(cookies=[f"session_id={session.session_id}"]), None)

    assert response["statusCode"] == 200
    assert "Set-Cookie" not in response["headers"]


def test_invalid_json_body_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch)

    response = lambda_handler(_event(body="{not valid json"), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"error": "invalid_request"}


def test_missing_message_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch)

    response = lambda_handler(_event(body=json.dumps({"not_message": "hi"})), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"error": "invalid_request"}


def test_malformed_base64_body_returns_400_with_cors_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch)

    response = lambda_handler(_event(body="%%%not-base64%%%", is_base64_encoded=True), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"error": "invalid_request"}
    assert response["headers"]["Access-Control-Allow-Origin"] == _ALLOWED_ORIGIN


def test_base64_body_with_invalid_utf8_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch)
    encoded = base64.b64encode(b"\xff\xfe").decode("ascii")

    response = lambda_handler(_event(body=encoded, is_base64_encoded=True), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"error": "invalid_request"}


def test_session_rate_limit_returns_429_with_zero_agent_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = RateLimitDecision(allowed=False, scope="session", retry_after_seconds=30)
    rate_limiter = FakeRateLimiter(decisions=[decision])
    agent_client = _install_container(monkeypatch, rate_limiter=rate_limiter)

    response = lambda_handler(_event(), None)

    assert response["statusCode"] == 429
    assert json.loads(response["body"]) == {"error": "rate_limited", "scope": "session"}
    assert response["headers"]["Retry-After"] == "30"
    assert agent_client.calls == []


def test_missing_source_ip_returns_400_with_zero_downstream_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    session_store = FakeSessionStore(FrozenClock(_NOW))
    rate_limiter = FakeRateLimiter()
    agent_client = FakeAgentClient()
    container = Container(
        session_store=session_store,
        rate_limiter=rate_limiter,
        agent_client=agent_client,
        clock=FrozenClock(_NOW),
        allowed_origins=(_ALLOWED_ORIGIN,),
    )
    monkeypatch.setattr(handler_module, "_container", container)
    event = _event()
    del event["requestContext"]["http"]["sourceIp"]

    response = lambda_handler(event, None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"error": "invalid_request"}
    assert session_store.records == {}
    assert rate_limiter.calls == []
    assert agent_client.calls == []


def test_ip_rate_limit_returns_429_with_zero_agent_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    rate_limiter = FakeRateLimiter(decisions=[RateLimitDecision(allowed=False, scope="ip", retry_after_seconds=5)])
    agent_client = _install_container(monkeypatch, rate_limiter=rate_limiter)

    response = lambda_handler(_event(), None)

    assert response["statusCode"] == 429
    assert json.loads(response["body"]) == {"error": "rate_limited", "scope": "ip"}
    assert agent_client.calls == []


def test_rate_limited_log_uses_extracted_message_not_raw_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    decision = RateLimitDecision(allowed=False, scope="session", retry_after_seconds=30)
    rate_limiter = FakeRateLimiter(decisions=[decision])
    _install_container(monkeypatch, rate_limiter=rate_limiter)

    with caplog.at_level(logging.INFO, logger="api.handler"):
        response = lambda_handler(_event(), None)

    assert response["statusCode"] == 429
    log_event = json.loads(caplog.records[0].message)
    assert log_event["message_prefix"] == _MESSAGE


def test_disallowed_origin_omits_cors_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch)

    response = lambda_handler(_event(origin="https://evil.example.com"), None)

    assert response["statusCode"] == 200
    assert "Access-Control-Allow-Origin" not in response["headers"]


def test_log_line_never_contains_raw_session_id_ip_or_full_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_container(monkeypatch)
    long_message = "a" * 300

    with caplog.at_level(logging.INFO, logger="api.handler"):
        response = lambda_handler(_event(body=json.dumps({"message": long_message})), None)

    assert response["statusCode"] == 200
    assert len(caplog.records) == 1
    log_event = json.loads(caplog.records[0].message)
    assert log_event["status"] == 200
    assert len(log_event["message_prefix"]) <= 100
    assert long_message not in log_event["message_prefix"] or len(long_message) <= 100
    assert _SOURCE_IP not in json.dumps(log_event)
    for cookie_header in response["headers"].get("Set-Cookie", "").split(";"):
        raw_session_id = cookie_header.strip().removeprefix("session_id=")
        if raw_session_id and raw_session_id != cookie_header.strip():
            assert raw_session_id not in json.dumps(log_event)


def test_contract_end_to_end_with_real_dynamodb_adapters(monkeypatch: pytest.MonkeyPatch, dynamodb_table: Any) -> None:
    """Light contract test: real `DynamoSessionStore`/`DynamoRateLimiter` against
    a moto-backed table, a fake `AgentClient` (no network), driven end-to-end
    through `lambda_handler`.
    """
    clock = FrozenClock(_NOW)
    container = Container(
        session_store=DynamoSessionStore(dynamodb_table, clock, SecureIds()),
        rate_limiter=DynamoRateLimiter(dynamodb_table, clock),
        agent_client=FakeAgentClient(answer=AgentAnswer(answer="Grounded answer.", language="en")),
        clock=clock,
        allowed_origins=(_ALLOWED_ORIGIN,),
    )
    monkeypatch.setattr(handler_module, "_container", container)

    response = lambda_handler(_event(), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"answer": "Grounded answer.", "language": "en"}
    assert "Set-Cookie" in response["headers"]
