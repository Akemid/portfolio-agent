"""End-to-end error mapping through `handler.lambda_handler` (`chat-endpoint`
spec, *Upstream Failure Mapping*). Closes the seam between `answer_question`'s
raised domain errors (via a fake `AgentClient`) and `responder.error_response`'s
HTTP mapping — the individual pieces are each already unit-tested; this proves
they compose correctly end-to-end through the real handler.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes.fake_agent_client import FakeAgentClient
from fakes.fake_rate_limiter import FakeRateLimiter
from fakes.fake_session_store import FakeSessionStore
from fakes.frozen_clock import FrozenClock

import api.handler as handler_module
from api.domain.errors import UpstreamError, UpstreamTimeout
from api.handler import Container

_NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
_ALLOWED_ORIGIN = "https://sergiomondragon.com"


def _event() -> dict[str, Any]:
    return {
        "requestContext": {"http": {"method": "POST", "path": "/v1/chat", "sourceIp": "203.0.113.7"}},
        "headers": {"origin": _ALLOWED_ORIGIN},
        "cookies": [],
        "body": json.dumps({"message": "What is your experience with React?"}),
        "isBase64Encoded": False,
    }


def _install_container(monkeypatch: pytest.MonkeyPatch, agent_client: Any) -> None:
    container = Container(
        session_store=FakeSessionStore(FrozenClock(_NOW)),
        rate_limiter=FakeRateLimiter(),
        agent_client=agent_client,
        clock=FrozenClock(_NOW),
        allowed_origins=(_ALLOWED_ORIGIN,),
    )
    monkeypatch.setattr(handler_module, "_container", container)


def test_upstream_error_maps_to_502_without_leaking_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    error = UpstreamError("boto3 ClientError: arn:aws:iam::123456789012:role/secret")
    _install_container(monkeypatch, FakeAgentClient(error=error))

    response = handler_module.lambda_handler(_event(), None)

    assert response["statusCode"] == 502
    assert json.loads(response["body"]) == {"error": "upstream_error"}
    assert "arn:aws" not in response["body"]


def test_upstream_timeout_maps_to_504_without_leaking_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    error = UpstreamTimeout("timed out after 12s calling bedrock-agentcore")
    _install_container(monkeypatch, FakeAgentClient(error=error))

    response = handler_module.lambda_handler(_event(), None)

    assert response["statusCode"] == 504
    assert json.loads(response["body"]) == {"error": "upstream_timeout"}
    assert "bedrock-agentcore" not in response["body"]


def test_unexpected_exception_maps_to_generic_500_without_traceback_in_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch, FakeAgentClient(error=RuntimeError("unexpected boom with secret detail")))

    response = handler_module.lambda_handler(_event(), None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"error": "internal_error"}
    assert "secret detail" not in response["body"]
    assert "Traceback" not in response["body"]
