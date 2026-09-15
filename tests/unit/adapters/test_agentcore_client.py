"""Unit tests for `AgentCoreClient` and `build_agentcore_client`.

Covers `agent-runtime` spec (Hosting and Invocation; Invocation Payload
Contract) and `chat-endpoint` spec (Upstream Failure Mapping). No network:
`bedrock-agentcore` is faked with a hand-written client double per
`ArchitecturalConstraint` — botocore's `Stubber` cannot validate the
streaming response shape for this service model without a real connection.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, IncompleteReadError, ReadTimeoutError

from api.adapters.agentcore_client import AgentCoreClient, build_agentcore_client
from api.domain.errors import UpstreamError, UpstreamTimeout
from api.domain.hashing import derive_key
from api.domain.models import AgentAnswer

_ARN = "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test-agent"
_RUNTIME_SESSION_ID = derive_key("rt", "opaque-cookie-value")


class _FakeStreamingBody:
    """Minimal stand-in for botocore's `StreamingBody` — only `.read()` is used."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _RaisingStreamingBody:
    """A streaming body whose `.read()` raises mid-stream, as botocore's real
    `StreamingBody` can (`ReadTimeoutError`, `IncompleteReadError`, etc.)."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def read(self) -> bytes:
        raise self._error


class _FakeBotoClient:
    """Records every call and returns a scripted response or raises a scripted error."""

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _response_for(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "contentType": "application/json",
        "response": _FakeStreamingBody(json.dumps(payload).encode()),
    }


def test_runtime_session_id_is_64_hex_chars() -> None:
    assert len(_RUNTIME_SESSION_ID) == 64
    assert all(c in "0123456789abcdef" for c in _RUNTIME_SESSION_ID)


def test_payload_uses_prompt_key() -> None:
    client = _FakeBotoClient(response=_response_for({"answer": "hi", "language": "en"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    adapter.ask("What is your experience with React?", _RUNTIME_SESSION_ID)

    call = client.calls[0]
    assert json.loads(call["payload"]) == {"prompt": "What is your experience with React?"}
    assert call["contentType"] == "application/json"
    assert call["accept"] == "application/json"
    assert call["agentRuntimeArn"] == _ARN
    assert call["runtimeSessionId"] == _RUNTIME_SESSION_ID


def test_qualifier_is_omitted_when_not_configured() -> None:
    client = _FakeBotoClient(response=_response_for({"answer": "hi", "language": "en"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    adapter.ask("hello", _RUNTIME_SESSION_ID)

    assert "qualifier" not in client.calls[0]


def test_qualifier_is_forwarded_when_configured() -> None:
    client = _FakeBotoClient(response=_response_for({"answer": "hi", "language": "en"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN, qualifier="PROD")

    adapter.ask("hello", _RUNTIME_SESSION_ID)

    assert client.calls[0]["qualifier"] == "PROD"


def test_happy_path_parses_answer_and_language() -> None:
    client = _FakeBotoClient(response=_response_for({"answer": "I built X.", "language": "es"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    result = adapter.ask("cuentame sobre tu experiencia", _RUNTIME_SESSION_ID)

    assert result == AgentAnswer(answer="I built X.", language="es")


def test_missing_answer_key_raises_upstream_error() -> None:
    client = _FakeBotoClient(response=_response_for({"language": "en"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_missing_language_key_raises_upstream_error() -> None:
    client = _FakeBotoClient(response=_response_for({"answer": "hi"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_unsupported_language_raises_upstream_error() -> None:
    client = _FakeBotoClient(response=_response_for({"answer": "bonjour", "language": "fr"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_non_string_answer_raises_upstream_error() -> None:
    client = _FakeBotoClient(response=_response_for({"answer": 12345, "language": "en"}))
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_malformed_json_body_raises_upstream_error() -> None:
    client = _FakeBotoClient(response={"contentType": "application/json", "response": _FakeStreamingBody(b"not json")})
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_non_object_json_body_raises_upstream_error() -> None:
    client = _FakeBotoClient(response={"contentType": "application/json", "response": _FakeStreamingBody(b"[1, 2, 3]")})
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_response_missing_body_key_raises_upstream_error() -> None:
    client = _FakeBotoClient(response={"contentType": "application/json"})
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_streaming_read_timeout_maps_to_upstream_timeout_without_leaking_endpoint() -> None:
    body_error = ReadTimeoutError(endpoint_url="https://should-not-leak.example")
    client = _FakeBotoClient(
        response={"contentType": "application/json", "response": _RaisingStreamingBody(body_error)}
    )
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamTimeout) as exc_info:
        adapter.ask("hello", _RUNTIME_SESSION_ID)

    assert "should-not-leak.example" not in str(exc_info.value)


def test_streaming_incomplete_read_maps_to_upstream_error() -> None:
    body_error = IncompleteReadError(actual_bytes=5, expected_bytes=10)
    client = _FakeBotoClient(
        response={"contentType": "application/json", "response": _RaisingStreamingBody(body_error)}
    )
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_client_error_maps_to_upstream_error() -> None:
    error = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "InvokeAgentRuntime")
    client = _FakeBotoClient(error=error)
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError) as exc_info:
        adapter.ask("hello", _RUNTIME_SESSION_ID)

    message = str(exc_info.value).lower()
    assert "throttling" not in message
    assert "slow down" not in message
    assert _ARN not in str(exc_info.value)


def test_access_denied_client_error_maps_to_upstream_error() -> None:
    error = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, "InvokeAgentRuntime")
    client = _FakeBotoClient(error=error)
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamError):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_timeout_maps_to_upstream_timeout() -> None:
    error = ReadTimeoutError(endpoint_url="https://bedrock-agentcore.us-east-1.amazonaws.com/")
    client = _FakeBotoClient(error=error)
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamTimeout) as exc_info:
        adapter.ask("hello", _RUNTIME_SESSION_ID)

    assert "https://" not in str(exc_info.value)


def test_connect_timeout_maps_to_upstream_timeout() -> None:
    error = ConnectTimeoutError(endpoint_url="https://bedrock-agentcore.us-east-1.amazonaws.com/")
    client = _FakeBotoClient(error=error)
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    with pytest.raises(UpstreamTimeout):
        adapter.ask("hello", _RUNTIME_SESSION_ID)


def test_no_logging_and_no_ids_or_secrets_leaked_on_client_error(caplog: pytest.LogCaptureFixture) -> None:
    error = ClientError(
        {"Error": {"Code": "ValidationException", "Message": f"bad runtimeSessionId {_RUNTIME_SESSION_ID}"}},
        "InvokeAgentRuntime",
    )
    client = _FakeBotoClient(error=error)
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)
    secret_prompt = "super secret prompt content"

    with caplog.at_level(logging.DEBUG), pytest.raises(UpstreamError) as exc_info:
        adapter.ask(secret_prompt, _RUNTIME_SESSION_ID)

    assert caplog.records == []
    message = str(exc_info.value)
    assert secret_prompt not in message
    assert _RUNTIME_SESSION_ID not in message


def test_build_agentcore_client_sets_cost_and_latency_aware_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserts on the `Config` we build, not on botocore's internal retry-resolution
    shape (which normalizes `max_attempts` differently per retry mode once attached
    to a real client — an implementation detail, not part of our contract).
    """
    captured: dict[str, Any] = {}

    def _fake_boto3_client(service_name: str, **kwargs: Any) -> str:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return "fake-client"

    monkeypatch.setattr("api.adapters.agentcore_client.boto3.client", _fake_boto3_client)

    result = build_agentcore_client(region="us-east-1")

    assert result == "fake-client"
    assert captured["service_name"] == "bedrock-agentcore"
    assert captured["region_name"] == "us-east-1"
    config = captured["config"]
    assert config.connect_timeout == 3.0
    assert config.read_timeout == 12.0
    assert config.retries == {"total_max_attempts": 1, "mode": "standard"}


def test_build_agentcore_client_resolves_to_exactly_one_total_attempt() -> None:
    """botocore normalizes `retries={"max_attempts": N}` to `total_max_attempts = N + 1`
    (client-config `max_attempts` always means *retry* attempts, on top of the initial
    call) regardless of retry mode -- so `max_attempts=1` alone resolves to 2 total
    attempts, not 1. Only an explicit `total_max_attempts=1` (with `mode="standard"`,
    which is what actually honors it) resolves to a single, non-retried attempt. This
    builds a real `boto3` client (no network call — construction is local) and asserts
    on the *resolved* `client.meta.config.retries`, not on the `Config` we pass in.
    """
    client = build_agentcore_client(region="us-east-1")

    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client.meta.config.retries["mode"] == "standard"


def test_build_agentcore_client_accepts_custom_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_boto3_client(service_name: str, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "fake-client"

    monkeypatch.setattr("api.adapters.agentcore_client.boto3.client", _fake_boto3_client)

    build_agentcore_client(region="us-east-1", connect_timeout=1.5, read_timeout=8.0)

    config = captured["config"]
    assert config.connect_timeout == 1.5
    assert config.read_timeout == 8.0
